# Proposed documentation information architecture

This outline is a proposal only. Full documentation writing begins after user
approval, in accordance with the selected documentation-writer workflow.

## Root README: product landing page

`README.md` and `README.zh-CN.md` use the same section order:

1. **作品名称与介绍** — “Notebook Agent” and a concise product promise:
   turn saved videos into private knowledge that can be recalled, checked, and
   reopened at the original timestamp; link to the live product page and show
   its public hero image.
2. **需求从哪里开始** — show why an ordinary bookmark list fails when the
   reader needs to find, reuse, and verify something they watched before.
3. **目标用户与真实需要** — deep learners, research/product work, creators,
   and privacy-conscious self-hosters; describe the job each group is trying to
   complete rather than demographic labels alone.
4. **具体使用场景** — at minimum: recalling a point from a long course,
   comparing views across interviews, collecting an authenticated course video
   through the browser companion, and asking from an existing chat/MCP surface.
5. **Agent 核心能力与使用流程** — save, parse and index, retrieve within the
   user's library, compose only from evidence, and return citations/timestamps.
   Present this as four user steps and support it with the public flow image.
   Optional interfaces are entry points, not separate product cores.
6. **产品体验与入口** — first show what an evidence-backed answer looks like,
   then explain the Web library, MCP/chat surfaces, browser companion, and
   optional channel integrations. Use the public preset-demo image and identify
   it accurately as a non-model, non-uploading demonstration.
7. **可信边界与当前限制** — tenant isolation, evidence rules, supported
   source matrix, optional components, and features that are not shipped.
8. **开始使用与文档路径** — short self-hosting route, then links for using,
    integrating, operating, understanding, and contributing.
9. **项目状态** — hackathon context, maturity expectations, and license.

## Public product-page visuals

- Store the three approved captures under `docs/assets/readme/` so the README
  does not depend on remote image delivery.
- Use only the public landing page. Do not capture the authenticated library,
  account data, cookies, or other private browser state.
- Link the hero image to the product home, the process image to `#process`, and
  the answer image to `#demo`.
- Keep captions and surrounding explanations semantically aligned in English
  and Simplified Chinese. The English README may note that the public page is
  currently presented in Chinese.

Do not add anonymous testimonials, an unverified founder story, or a product
validation report to the README. The product promise, interaction, and limits
must stand on their own.

## Documentation tree

```text
docs/
├── README.md                          # Chinese documentation home and route map
├── tutorials/
│   └── first-run.md                   # First local read-only MCP success
├── how-to/
│   ├── README.md                      # Goal-oriented guide index
│   ├── run-full-library.md            # Enable saving and background ingestion
│   ├── use-web-library.md             # Login, save, search, inspect, manage
│   ├── connect-mcp-client.md           # Issue a grant and connect stdio/HTTP
│   ├── use-browser-companion.md        # Install, pair, capture, revoke
│   ├── connect-langbot.md              # Add Telegram/WeChat and link identities
│   ├── deploy-production.md            # General secure production rollout
│   ├── deploy-frontend.md              # Bundled and split Web deployment
│   ├── back-up-and-restore.md           # Data and object-store recovery recipe
│   ├── upgrade-and-roll-back.md         # Paired release and migration procedure
│   └── troubleshoot.md                 # Symptom-first operational recovery
├── reference/
│   ├── README.md                       # Reference index and ownership notes
│   ├── runtime-profiles.md              # read/full/langbot component matrix
│   ├── configuration.md                 # Environment variable dictionary
│   ├── cli.md                           # Launcher and app CLI command reference
│   ├── mcp.md                           # Transports, grants, scopes, tool surface
│   └── web-api.md                       # Authentication and HTTP/SSE contracts
├── explanation/
│   ├── README.md                       # Concept index
│   ├── architecture.md                 # Components, data flow, deployment boundary
│   ├── ingestion-and-retrieval.md       # From captions to grounded answers
│   ├── privacy-and-trust.md             # Tenant, evidence, secret, capture boundaries
│   └── identities-and-channels.md       # Web/MCP/channel identity model
└── operations/
    └── production/                     # Environment-specific runbooks retained
        ├── README.md
        ├── debian-nginx-frontend.md
        ├── ovh-caddy.md
        ├── langbot-telegram.md
        └── youtube-home-egress.md
```

## Document-type boundaries

- The tutorial gives one safe path and a visible success result. It does not
  enumerate alternatives or every setting.
- How-to guides assume a goal and provide a recipe. They link to reference
  pages for option definitions and to explanation pages for rationale.
- Reference pages describe current interfaces and configuration without leading
  readers through a narrative deployment.
- Explanation pages describe why the system is structured this way; they do not
  become installation checklists.
- Environment-specific production runbooks remain grouped as operations because
  their value is exact host/provider procedure, not general product onboarding.

## Existing-content migration

| Current location | Proposed destination |
| --- | --- |
| `docs/getting-started/README.md` | `docs/tutorials/first-run.md` |
| `docs/getting-started/configuration.md` scenarios | relevant how-to guides |
| `docs/getting-started/configuration.md` variable table | `docs/reference/configuration.md` |
| `docs/interfaces/README.md` MCP section | `docs/how-to/connect-mcp-client.md` + `docs/reference/mcp.md` |
| `docs/interfaces/web-api.md` | `docs/reference/web-api.md` |
| `docs/integrations/README.md` | `docs/how-to/connect-langbot.md` + explanation links |
| `docs/deployment/README.md` | production, backup, upgrade, troubleshooting how-to guides + architecture/reference pages |
| `docs/deployment/frontend.md` | `docs/how-to/deploy-frontend.md` |
| `docs/deployment/production/*` | `docs/operations/production/*` |

The migration table is a coverage map, not permission to copy sections without
rechecking their purpose and current accuracy.

## Language and terminology

- Root README language remains parallel English and Simplified Chinese.
- `docs/` headings and explanatory prose use Simplified Chinese.
- Code identifiers and protocol names remain exact: `read`, `full`, `langbot`,
  Streamable HTTP, MCP, grant, scope, tenant, Celery, and environment variables.
- Use “资料库” for the user-visible collection, “知识库” for the broader product
  concept where appropriate, and “原文依据” or “引用依据” for retrieved evidence.
