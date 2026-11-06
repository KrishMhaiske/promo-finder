"""Streamlit UI: upload a show's screenplays -> ranked promo clips."""
import io
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from promo_finder.llm import LLM
from promo_finder.parser import parse_text
from promo_finder.pipeline import parse_episode_spec, run

st.set_page_config(page_title="Promo Finder", page_icon="🎬", layout="wide")
st.title("🎬 Promo Finder — microdrama screenplay → ranked promo clips")
st.caption("Reads timestamped screenplays and returns 20–60s promo clips (hook in first 3s, cliffhanger "
           "ending, standalone), with exact segment timestamps, ranked by what to post first.")


def secret(name, default=""):
    try:
        return st.secrets.get(name, os.getenv(name, default))
    except Exception:
        return os.getenv(name, default)


# On Streamlit Cloud, settings live in Secrets. Copy them into the environment so the pipeline's
# provider-specific limits (BATCH_EPISODES, JUDGE_BATCH, LLM_FALLBACK_MODELS, ...) apply there too.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)) and _k not in os.environ:
            os.environ[_k] = str(_v)
except Exception:  # no secrets file locally -> .env is used instead
    pass


with st.sidebar:
    st.header("Model")
    # defaults come from the server's secrets / .env, so the deployed app uses whatever provider it was set up with
    providers = ["gemini", "openai_compatible"]
    default_provider = secret("LLM_PROVIDER", "gemini")
    provider = st.selectbox("Provider", providers,
                            index=providers.index(default_provider) if default_provider in providers else 0)
    same = provider == default_provider
    model = st.text_input("Model", secret("LLM_MODEL", "") if same and secret("LLM_MODEL", "") else
                          ("gemini-3.6-flash" if provider == "gemini" else "openai/gpt-oss-120b"))
    base_url = st.text_input("Base URL", secret("LLM_BASE_URL", "https://api.groq.com/openai/v1")) \
        if provider != "gemini" else None
    key = st.text_input("API key (blank = use server key)", type="password") or \
        secret("GEMINI_API_KEY" if provider == "gemini" else "LLM_API_KEY")
    interval = st.number_input("Seconds between calls", 0.0, 120.0,
                               float(secret("LLM_MIN_INTERVAL", "6.5" if provider == "gemini" else "40")),
                               help="Free tiers: Gemini ~10 requests/min; Groq 8k tokens/min (use ~40s).")
    st.caption(f"Server key for this provider: {'set' if secret('GEMINI_API_KEY' if provider == 'gemini' else 'LLM_API_KEY') else 'not set'}")

tab_run, tab_view = st.tabs(["Run on new screenplays", "Browse saved results"])


def show_results(result: dict):
    m = result["meta"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Episodes", m["episodes_processed"])
    c2.metric("Candidates (valid)", f"{m['candidates_total']} ({m['candidates_valid']})")
    c3.metric("Ranked promos", m["ranked"])
    c4.metric("Model", m["model"])
    ranked = result["ranked"]
    by_id = {r["id"]: r for r in ranked}

    def row(r):
        segs = " + ".join(f"Ep{s['episode']} {s['start']}–{s['end']}" for s in r["segments"])
        sc = r.get("scores") or {}
        return {"rank": r["rank"], "id": r["id"], "score": r["final_score"], "length (s)": round(r["duration_sec"]),
                "segments": segs, "hook": r["hook"], "cliffhanger": r["cliffhanger"], "why": r["why"],
                "H/C/S/A/Spoil": f"{sc.get('hook')}/{sc.get('cliffhanger')}/{sc.get('standalone')}/"
                                 f"{sc.get('appeal')}/{sc.get('spoiler')}"}

    st.subheader("📌 Post these first")
    reasons = result.get("posting_plan_reasons", {})
    st.dataframe(pd.DataFrame([{**row(by_id[i]), "why post first": reasons.get(i, "")}
                               for i in result["posting_plan"] if i in by_id]),
                 hide_index=True, use_container_width=True)
    st.subheader("All ranked promos")
    st.dataframe(pd.DataFrame([row(r) for r in ranked]), hide_index=True, use_container_width=True)
    pick = st.selectbox("Inspect a promo", [f"{r['rank']}. {r['id']} — {r['title']}" for r in ranked])
    if pick:
        r = ranked[int(pick.split(".")[0]) - 1]
        st.markdown(f"**Why:** {r['why']}  \n**Judge:** {(r.get('scores') or {}).get('verdict')}  \n"
                    f"**Context gap:** {r['standalone_gap'] or '—'}  \n**Auto-fixes:** {'; '.join(r['fixes']) or '—'}")
        st.code(r["clip_text"], language=None)
    with st.expander(f"Rejected / duplicates ({len(result['rejected_or_duplicate'])})"):
        st.dataframe(pd.DataFrame([{"id": r["id"], "length": round(r["duration_sec"]), "issues": "; ".join(r["issues"])}
                                   for r in result["rejected_or_duplicate"]]), hide_index=True)
    st.download_button("Download promos.json", json.dumps(result, indent=2, ensure_ascii=False), "promos.json")


with tab_run:
    files = st.file_uploader("Episode screenplays (.txt files or one .zip)", type=["txt", "zip"],
                             accept_multiple_files=True)
    title = st.text_input("Show title", "Untitled show")
    ep_spec = st.text_input("Episodes to search (blank = all)", "", placeholder="e.g. 7  or  5-10,12",
                            help="Upload the whole show so the model gets story context, then pick episodes here.")
    if st.button("Find promos", type="primary", disabled=not files):
        texts = []
        for f in files:
            if f.name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(f.read())) as z:
                    texts += [(n, z.read(n).decode("utf-8", "replace")) for n in z.namelist() if n.lower().endswith(".txt")]
            else:
                texts.append((f.name, f.read().decode("utf-8", "replace")))
        episodes = []
        for name, txt in texts:
            m = re.search(r"episode-(\d+)", Path(name).name, re.I)
            episodes.append(parse_text(txt, int(m.group(1)) if m else 0, Path(name).name))
        episodes.sort(key=lambda e: e.number)
        if not key:
            st.error("Add an API key in the sidebar.")
            st.stop()
        llm = LLM(provider=provider, model=model, api_key=key, base_url=base_url, min_interval=interval)
        logbox = st.empty()
        logs = []

        def log(msg):
            logs.append(msg)
            logbox.code("\n".join(logs[-15:]))

        only = parse_episode_spec(ep_spec) if ep_spec.strip() else None
        with st.spinner("Finding promos…"):
            out = Path(tempfile.mkdtemp())
            try:
                result = run(episodes, title, out, llm=llm, only=only, log=log)
            except ValueError as e:
                st.error(str(e))
                st.stop()
        st.session_state["result"] = result
    if "result" in st.session_state:
        show_results(st.session_state["result"])

with tab_view:
    st.caption("Open a saved run: drop a promos.json from the raw outputs folder, or pick one found on disk.")
    up = st.file_uploader("promos.json", type=["json"], key="view_json")
    saved = sorted(Path("outputs").glob("*/promos.json"))
    if up is not None:
        show_results(json.loads(up.read().decode("utf-8")))
    elif saved:
        choice = st.selectbox("Saved run", saved, format_func=lambda p: p.parent.name)
        show_results(json.loads(Path(choice).read_text(encoding="utf-8")))
    else:
        st.info("No saved runs here. Upload a promos.json above.")
