# Notebook Agent

[English](README.md) | [简体中文](README.zh-CN.md)

> Turn saved videos into private knowledge you can find, verify, and reopen at the original source.

Notebook Agent is a private AI agent for video learning and research. It turns video links and captions into a searchable library that belongs to you: save and browse content in the Web library, or ask through MCP and optional chat entry points. Answers include retrieved source evidence and timestamps instead of an untraceable summary.

[Explore the product and public demo →](https://notebookai.deequoique.tech/)

[![Notebook Agent product homepage: make saved knowledge useful again](docs/assets/readme/product-home.jpg)](https://notebookai.deequoique.tech/)

**EAZO Global Hackathon Project**

## Why Notebook Agent

People bookmark courses, interviews, talks, and industry analysis, then later remember only a vague idea—not the title, source, or exact moment where it was explained.

A bookmark list proves that something was saved, but it cannot answer “what did it say?”, “where is the evidence?”, or “how do I get back to the source quickly?”. Notebook Agent takes the next step after saving a link: it turns the video into personal knowledge that can be searched, questioned, checked, and reused.

## Who it is for

- **Deep learners and students:** find a concept and its context again while reviewing a long course or lecture.
- **Researchers, product teams, and knowledge workers:** compare ideas across interviews, talks, and case studies while keeping verifiable sources.
- **Content creators:** relocate arguments, examples, and phrasing in material they have already watched without replaying every video.
- **Privacy-conscious and self-hosting users:** keep libraries isolated and access them through a familiar Web, MCP, or chat surface.

The shared need is a complete path: save once, let the system organize the source, ask in natural language, see the evidence, and return to the original video when context matters.

## Concrete scenarios

### Recall one idea from a long course

You remember that an instructor explained a concept but not which lecture or timestamp. Ask Notebook Agent, inspect the matching caption evidence, and jump back to the source to continue learning.

### Compare ideas across interviews

After saving several interviews, ask what the speakers agree or disagree about. Notebook Agent keeps the video source for each result so you can read and verify the context.

### Save a course page that requires your login

When a course page cannot be read directly by the server, the optional browser companion can read captions from the page in a browser session you have already authorized and submit them to the same private library. Page credentials, cookies, and signed caption URLs are not uploaded as server-fetch jobs.

### Ask from a surface you already use

The Web library and MCP are the core entry points. If you already work in Telegram or WeChat, you can add the optional LangBot bridge. All entry points can reach the same private library while keeping the same tenant boundary.

## From saved video to verifiable answer in four steps

Notebook Agent is not another link list. It closes a traceable loop from source to answer.

[![Notebook Agent's four-step flow: submit a source, build an index, search your library, and receive an evidence-backed answer](docs/assets/readme/product-flow.jpg)](https://notebookai.deequoique.tech/#process)

1. **Submit and archive a video source:** save a video link from the Web library or an enabled chat entry point, with a note about why it matters.
2. **Parse and index it asynchronously:** the system extracts titles, chapters, and captions in the background, then splits long content into searchable passages while you continue browsing.
3. **Search your private library:** ask in natural language; retrieval stays inside the current user's library and assembles the relevant source context.
4. **Receive an evidence-backed answer:** the answer includes video titles, quoted excerpts, and jumpable timestamps so you can check the full context in the original video.

The same flow also provides:

- A Web library for batch URL saving, notes, title/author/note search, item and transcript views, archive/restore, and retrying failed items.
- Hybrid PostgreSQL full-text and pgvector semantic retrieval within the current user's space.
- Server-validated citations with source titles, real URLs, excerpts, and timestamps. When the library does not contain enough evidence, the answer stays bounded instead of filling the gap from model memory.
- Standard MCP entry points over `stdio` and Streamable HTTP. Each client uses a scoped grant: `read` covers questions and library reads; `full` is required for saving and other mutations.
- An optional browser companion for supported page captures, and an optional LangBot bridge for Telegram and WeChat.

## Product surfaces

### What an answer looks like

A complete answer contains more than a conclusion. It shows how many caption passages supported the response, the corresponding source evidence, and timestamps that take you back to the video. Read the answer first, then verify each point without replaying the entire source.

[![Notebook Agent public demo showing an answer with three verifiable video timestamps](docs/assets/readme/product-evidence-demo.jpg)](https://notebookai.deequoique.tech/#demo)

The image above comes from the product page's public preset demo. It does not call a model or upload data; it only demonstrates the path from question to answer to source timestamp. In normal use, the preset source is replaced by content from your own library. The current public product page is presented in Chinese.

### Web library

After login, the Web library is the end-to-end place to save, find, and manage content. New submissions appear in a processing queue first and move into the readable library when ready. You can inspect titles, authors, notes, captions, and source details, then retry, archive, or restore items.

Web conversations keep answers and evidence together. The server renders source titles, URLs, excerpts, and video timestamps from the current retrieval result so you can move from an answer back to the video.

### MCP

MCP connects desktop agents, automation, and other MCP clients through `stdio` or Streamable HTTP. Grants are bound to a user space and scoped as `read` or `full`; the available tools follow that scope. MCP Bearer credentials do not authenticate Web cookies, and a client cannot choose another user's library through a tool argument.

### Browser companion

The browser companion is an optional Chrome/Chromium extension. After pairing and approving it in the Web account, it can read captions from supported YouTube and NTULearn/Kaltura pages and submit normalized captions to Notebook Agent. It is not a generic authenticated-site scraper or a media uploader, and it does not send page cookies, playback credentials, or signed caption URLs to the server. Paired devices can be listed and revoked from the Web account.

### Telegram and WeChat

Telegram and WeChat are optional LangBot bridge channels, not prerequisites for the core runtime. Cross-channel linking uses a single-use code bound to the target channel. Once linked, both channels reach the same user space while their conversation histories remain separate.

## Trust boundaries and current limits

| Capability | Current status |
| --- | --- |
| YouTube | Server-side ingestion for ordinary video URLs; it depends on readable captions and does not invent verifiable content when no caption is available. |
| Bilibili | Server-side ingestion for ordinary video URLs, using only captions accessible without persisting an account cookie. Login-only or server-invisible captions cannot be bypassed by the connector. |
| Browser companion | Currently adapted to YouTube and specific NTULearn/Kaltura pages; it is not support for arbitrary websites or a general audio/video uploader. |
| Evidence and answers | Citations come only from evidence retrieved from the current user's library during the current run; sources and timestamps are rendered by the server. Insufficient evidence produces a bounded result. |
| Isolation and channels | Web, MCP, Telegram, and WeChat stay within the user-space boundary; LangBot and the browser companion are optional components. |
| Not shipped as a general feature | ASR is not a generally available ingestion path, and WeChat article ingestion is not implemented. |

Notebook Agent promises a more useful and verifiable path through sources you already saved. It does not promise access to every platform, automatic viewing of every video, or an answer when there is no caption evidence.

## Start here and choose a documentation path

### Short self-hosting route

To run the full save, organize, and ask workflow, you need Python 3.11+, Docker Compose, PostgreSQL, Redis, S3-compatible object storage, an Agent-model credential, and a Zhipu Embedding API credential. The managed lifecycle launcher supports Linux and macOS; Windows users should follow the direct-start deployment guide.

From the project root:

```bash
git clone https://github.com/deequoique/notebook-agent.git
cd notebook-agent

python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

# Full runtime: MCP, background ingestion, and the optional channel gateway
./scripts/notebook-agent init --profile full
./scripts/notebook-agent start --profile full
```

For a read-only MCP connection to an existing library, choose `read`; it does not start Redis, MinIO, a worker, or Beat and does not run background ingestion. Choose `langbot` when you need the background/channel runtime without the public MCP endpoint. Before connecting a client, follow the first-run tutorial to create a user and issue a scoped grant with the same private environment as the managed runtime.

Continue by goal:

- **First successful run:** [First-run tutorial](docs/tutorials/first-run.md)
- **Use the Web library or browser companion:** [How-to guides](docs/how-to/README.md) · [Browser companion guide](docs/how-to/use-browser-companion.md)
- **Connect MCP, Telegram, or WeChat:** [How-to guides](docs/how-to/README.md)
- **Deploy, back up, upgrade, or troubleshoot:** [How-to guides](docs/how-to/README.md) · [Operations runbooks](docs/operations/production/README.md)
- **Look up profiles, configuration, and interfaces:** [Reference](docs/reference/README.md)
- **Understand architecture, retrieval, and privacy boundaries:** [Explanation](docs/explanation/README.md)
- **Browse all documentation:** [Documentation index](docs/README.md)

## Project status and license

Notebook Agent was built for the **EAZO Global Hackathon**. The current repository implements the core Web library, evidence-first answers, MCP, server-side YouTube/Bilibili connectors, and optional browser-companion and LangBot entry points; each deployment profile still has provider and platform reachability requirements documented in the runbooks.

The project metadata declares **Proprietary** licensing. This repository is not currently released under a standard open-source license.
