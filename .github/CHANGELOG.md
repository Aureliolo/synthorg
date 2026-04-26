# Changelog

## [0.7.3](https://github.com/Aureliolo/synthorg/compare/v0.7.2...v0.7.3) (2026-04-25)


### Features

* **cli:** per-version Highlights walk on upgrade + dev-channel commit list ([#1564](https://github.com/Aureliolo/synthorg/issues/1564)) ([#1572](https://github.com/Aureliolo/synthorg/issues/1572)) ([2416fd4](https://github.com/Aureliolo/synthorg/commit/2416fd444806233178aba67a48e36f1670a9f2a4))
* expand codebase-audit skill from 123 to 153 agents ([#1582](https://github.com/Aureliolo/synthorg/issues/1582)) ([282f49c](https://github.com/Aureliolo/synthorg/commit/282f49ccb652628652fb63ec8e16030abdfb47d2))
* META-MCP-3 -- live CRUD write facades for agents / tasks / workflows / meta (17 capability_gap sites) ([#1574](https://github.com/Aureliolo/synthorg/issues/1574)) ([214cf5e](https://github.com/Aureliolo/synthorg/commit/214cf5e065be333b887dff4c3714b463ce2c14bb))
* META-MCP-4 observability + memory + coordination facades (19 capability_gap sites) ([#1558](https://github.com/Aureliolo/synthorg/issues/1558)) ([5a4e672](https://github.com/Aureliolo/synthorg/commit/5a4e67295ef10d8e42cc5ca125303ca8c3c4d6cb))
* **web:** hygiene sweep -- API double-prefix fix + dashboard UX pass ([#1563](https://github.com/Aureliolo/synthorg/issues/1563)) ([3b0e18a](https://github.com/Aureliolo/synthorg/commit/3b0e18a8854e4f268a973ca870f50d938eea66db))


### Bug Fixes

* **async:** TOCTOU races + resource leaks in lifecycle and bus paths ([#1545](https://github.com/Aureliolo/synthorg/issues/1545)) ([13f3db6](https://github.com/Aureliolo/synthorg/commit/13f3db679a6ad9933bdca7b27b8c61d6b4a2258d))
* **ci:** jq sort-keys + CLA bot exempt + client-id migration + PAT sunset ([#1566](https://github.com/Aureliolo/synthorg/issues/1566)) ([cf962a9](https://github.com/Aureliolo/synthorg/commit/cf962a94bdc7677016f6f135548e9424d54022b3))
* **ci:** move artifact smoke tests to build time + delete release-events spam ([#1580](https://github.com/Aureliolo/synthorg/issues/1580)) ([7eb238c](https://github.com/Aureliolo/synthorg/commit/7eb238cc151565edbf7cccee7b4f1eb126ba01b0))
* **ci:** quote hex colors in labels.yml + make sync-labels manual-only ([#1589](https://github.com/Aureliolo/synthorg/issues/1589)) ([83d2210](https://github.com/Aureliolo/synthorg/commit/83d22106b923185039384049978beb248b699bae))
* **ci:** release-runner-setup composite action parse error (main hotfix) ([#1565](https://github.com/Aureliolo/synthorg/issues/1565)) ([cb49c7d](https://github.com/Aureliolo/synthorg/commit/cb49c7d1cc7490b45e52e54925861745b41ee21c))
* **ci:** set GH_REPO env in release-events to fix chronic post-merge failure ([#1577](https://github.com/Aureliolo/synthorg/issues/1577)) ([c207a25](https://github.com/Aureliolo/synthorg/commit/c207a2548023a82e33ba8b5531d88bddc576a704))
* **ci:** skip release-please CI + apko web config + copilot drift noise ([#1594](https://github.com/Aureliolo/synthorg/issues/1594)) ([2cccb6f](https://github.com/Aureliolo/synthorg/commit/2cccb6f2e8faf55463918b31435591e269c1556b))
* **ci:** unblock dev releases + surface workflow_run failures + harden notification sinks ([#1576](https://github.com/Aureliolo/synthorg/issues/1576)) ([f682328](https://github.com/Aureliolo/synthorg/commit/f682328053a343bed259e4ef3b138e40d1939c61))
* **ci:** unblock release pipeline + dev/prod parity for cursor secret ([#1591](https://github.com/Aureliolo/synthorg/issues/1591)) ([92a3d04](https://github.com/Aureliolo/synthorg/commit/92a3d0425cbae36d2906dd90492765fbfbbcf3dc))
* **ci:** unblock release-PR CI + drop unsupported apko `--update` flag ([#1593](https://github.com/Aureliolo/synthorg/issues/1593)) ([1e391f2](https://github.com/Aureliolo/synthorg/commit/1e391f28ab27a3df67179a1c431e31633dbe130c))
* **cli:** never silently fall back during synthorg update ([#1581](https://github.com/Aureliolo/synthorg/issues/1581)) ([97c0f83](https://github.com/Aureliolo/synthorg/commit/97c0f8326a183a5fd3282db6a06b1b67efa10e9e))
* **cli:** render update walk as inline section, not full screen ([#1584](https://github.com/Aureliolo/synthorg/issues/1584)) ([3075c17](https://github.com/Aureliolo/synthorg/commit/3075c17ae333702018b6bd983d0e7ab7912f4756))
* drop openapi.json from sitemap and improve reference.html SEO ([#1575](https://github.com/Aureliolo/synthorg/issues/1575)) ([fbbfb3e](https://github.com/Aureliolo/synthorg/commit/fbbfb3e74a6ffe73c76c34bea4f331162ff33841))


### Refactoring

* **ci:** fork-friendly CI + preflight setup checker ([#1586](https://github.com/Aureliolo/synthorg/issues/1586)) ([fed4aee](https://github.com/Aureliolo/synthorg/commit/fed4aeecae3da8e3fa8843e0e83b582e00d55d08))
* **ci:** GitHub App + full release-pipeline rework ([#1561](https://github.com/Aureliolo/synthorg/issues/1561)) ([5b11979](https://github.com/Aureliolo/synthorg/commit/5b119795653c0c11311e45d4cff7960771f958b7))
* pagination + N+1 + dedup + small CLI/data cleanups ([#1551](https://github.com/Aureliolo/synthorg/issues/1551)) ([284a00c](https://github.com/Aureliolo/synthorg/commit/284a00c07be4e1f127fe0b0954d7369e3e34595b))
* platform hygiene ([#1562](https://github.com/Aureliolo/synthorg/issues/1562) + [#1557](https://github.com/Aureliolo/synthorg/issues/1557) + [#1552](https://github.com/Aureliolo/synthorg/issues/1552)) ([#1573](https://github.com/Aureliolo/synthorg/issues/1573)) ([5c35697](https://github.com/Aureliolo/synthorg/commit/5c35697e8733337953f0f459dd460b2a69988260))


### Maintenance

* Lock file maintenance ([#1571](https://github.com/Aureliolo/synthorg/issues/1571)) ([6979f1d](https://github.com/Aureliolo/synthorg/commit/6979f1d190508a5f521b663c7a6a5b700f452abd))
* **main:** release 0.7.3 ([#1556](https://github.com/Aureliolo/synthorg/issues/1556)) ([dfd68bd](https://github.com/Aureliolo/synthorg/commit/dfd68bdffa99f5a002dc3b07539370f547e75c9f))
* portable everything-claude-code reviewer agents ([#1583](https://github.com/Aureliolo/synthorg/issues/1583)) ([df5fee2](https://github.com/Aureliolo/synthorg/commit/df5fee2bb7b50ec1544f0d1fd6d730139517f062))
* trim CLAUDE.md deep-dives to docs/reference, fix em-dash hook ([#1560](https://github.com/Aureliolo/synthorg/issues/1560)) ([6f7f6fd](https://github.com/Aureliolo/synthorg/commit/6f7f6fd0d763dcf14d62354c06dbc802e33081e0))
* Update Container dependencies ([#1567](https://github.com/Aureliolo/synthorg/issues/1567)) ([9c731bd](https://github.com/Aureliolo/synthorg/commit/9c731bd9317bc8321b5c8c444e93c4e98c077718))
* Update dependency cryptography to v47 ([#1570](https://github.com/Aureliolo/synthorg/issues/1570)) ([f2d25c4](https://github.com/Aureliolo/synthorg/commit/f2d25c4df144aeb2fb341a0f7a6f47bb673cd563))
* Update Python dependencies ([#1568](https://github.com/Aureliolo/synthorg/issues/1568)) ([573fa84](https://github.com/Aureliolo/synthorg/commit/573fa845d3fc5c118e294f048d682ad968671606))

## [0.7.2](https://github.com/Aureliolo/synthorg/compare/v0.7.1...v0.7.2) (2026-04-24)


### Features

* **api:** apply per_op_rate_limit to mutation endpoints + inflight concurrency (SEC-2) ([#1511](https://github.com/Aureliolo/synthorg/issues/1511)) ([f983a0f](https://github.com/Aureliolo/synthorg/commit/f983a0f1bbafceb76e8798c94692feb4d0468edf))
* **meta-mcp:** implement MCP handlers for all 15 domains (204 tools) ([#1525](https://github.com/Aureliolo/synthorg/issues/1525)) ([d624a15](https://github.com/Aureliolo/synthorg/commit/d624a155e7f8d29aec95f037dee6d4948d4d87eb))
* **meta-mcp:** META-MCP-2 -- service facades for MCP handler surface ([#1530](https://github.com/Aureliolo/synthorg/issues/1530)) ([ac0dde8](https://github.com/Aureliolo/synthorg/commit/ac0dde8fc339bfd1837458ca4a4966bcf6daca00))
* **settings:** CFG-1 settings bridge + kill switches + retention + default config ([#1512](https://github.com/Aureliolo/synthorg/issues/1512)) ([44289ee](https://github.com/Aureliolo/synthorg/commit/44289ee6defe81d1e8ed815e030a22e5ba7a0e35))
* **web:** dashboard UX polish -- foundation primitives + consistency + responsive + a11y + onboarding (WEB-2) ([#1506](https://github.com/Aureliolo/synthorg/issues/1506)) ([9c7c176](https://github.com/Aureliolo/synthorg/commit/9c7c176a051ee95627f513156ac265a317093902))
* **web:** WS/SSE resilience + store error contract + TS strictness (WEB-1) ([#1500](https://github.com/Aureliolo/synthorg/issues/1500)) ([8dad275](https://github.com/Aureliolo/synthorg/commit/8dad275b7804eaee36ed62505ab204fe3e15449f))


### Bug Fixes

* **ci:** clean up fine-tune matrix job names ([#1546](https://github.com/Aureliolo/synthorg/issues/1546)) ([30ec9a1](https://github.com/Aureliolo/synthorg/commit/30ec9a14c6dc830d53986e1bf910a86d49149ca7))
* **ci:** pin wrangler commit metadata for PR previews ([#1513](https://github.com/Aureliolo/synthorg/issues/1513)) ([ed903b4](https://github.com/Aureliolo/synthorg/commit/ed903b451c96feefb73f2e93cb5d018823f8449d))
* **ci:** use GITHUB_TOKEN for BSL Change Date commit so it is signed ([#1554](https://github.com/Aureliolo/synthorg/issues/1554)) ([904675a](https://github.com/Aureliolo/synthorg/commit/904675af8aafa6f9e75fa4d29171d9e2980276d4))
* close concurrency races and N+1 batch queries (COR-1) ([#1503](https://github.com/Aureliolo/synthorg/issues/1503)) ([ae7ed55](https://github.com/Aureliolo/synthorg/commit/ae7ed55065ec5dc0d43ec9c5f9e0358655dbcd4a))
* **security:** wrap untrusted content + pin LLM CompletionConfig at 7 call sites (SEC-1) ([#1543](https://github.com/Aureliolo/synthorg/issues/1543)) ([c61e7ac](https://github.com/Aureliolo/synthorg/commit/c61e7ace86b4f4c26e8bb1d510127c2844d767f6))


### Refactoring

* code hygiene omnibus -- types codegen + helpers + stubs + observability + UI + evals + monitoring (HYG-1) ([#1518](https://github.com/Aureliolo/synthorg/issues/1518)) ([4c39704](https://github.com/Aureliolo/synthorg/commit/4c39704f5845a016f919a08519b11fb6b9eaecbb))
* decouple engine/tools/approval + abstraction-swap readiness (ARC-1) ([#1510](https://github.com/Aureliolo/synthorg/issues/1510)) ([f2d6286](https://github.com/Aureliolo/synthorg/commit/f2d6286745badbcc86e5918c7e89a9f3bd56e18d))
* HYG-2 magic numbers + rate-limit policy registry + subsystem limits ([#1526](https://github.com/Aureliolo/synthorg/issues/1526)) ([32a50f2](https://github.com/Aureliolo/synthorg/commit/32a50f289733f09f4cb58802b07a6e7cea1528ac))
* HYG-3 orphan fixtures + TODO cleanup ([#1517](https://github.com/Aureliolo/synthorg/issues/1517)) ([#1527](https://github.com/Aureliolo/synthorg/issues/1527)) ([48c4977](https://github.com/Aureliolo/synthorg/commit/48c4977b193c7710493edb29a8f573dd4e16985a))
* pagination cursors + async lifecycle + health split + CI (OPS-1) ([#1514](https://github.com/Aureliolo/synthorg/issues/1514)) ([198e27d](https://github.com/Aureliolo/synthorg/commit/198e27d97eb5d5f57b2febadeebad29ff1636353))
* **persistence:** Postgres/SQLite parity -- schema, indices, conformance, boundary (PST-1) ([#1505](https://github.com/Aureliolo/synthorg/issues/1505)) ([bb3adf0](https://github.com/Aureliolo/synthorg/commit/bb3adf0c11707dd81b76e038cd8efe952b453b8c))


### Documentation

* public docs refresh + OpenAPI regen + scope creep cleanup (DOC-1) ([#1504](https://github.com/Aureliolo/synthorg/issues/1504)) ([a58e48a](https://github.com/Aureliolo/synthorg/commit/a58e48a6f10dcfca9be22da63be9cfcde3b5fdd4))


### CI/CD

* Pin dependencies ([#1507](https://github.com/Aureliolo/synthorg/issues/1507)) ([e18a23f](https://github.com/Aureliolo/synthorg/commit/e18a23f44f9043fbe7463c2b8176dfd2c8822011))
* sign all CI-generated commits via GitHub API ([#1553](https://github.com/Aureliolo/synthorg/issues/1553)) ([c269048](https://github.com/Aureliolo/synthorg/commit/c2690489fda2fa1cbca62af037d8df8dd513e9bb))
* tighten workflow permissions + environment protection ([#1542](https://github.com/Aureliolo/synthorg/issues/1542)) ([1084ab5](https://github.com/Aureliolo/synthorg/commit/1084ab52965eaf247491b001d8a1989e18f5ef85))
* Update googleapis/release-please-action action to v5 ([#1549](https://github.com/Aureliolo/synthorg/issues/1549)) ([7f213ba](https://github.com/Aureliolo/synthorg/commit/7f213bae346a12df2da4e205c160d8bddfab8c73))


### Maintenance

* **docker:** source Atlas CLI from arigaio/atlas distroless image (fixes main Docker build) ([#1502](https://github.com/Aureliolo/synthorg/issues/1502)) ([b122563](https://github.com/Aureliolo/synthorg/commit/b122563ed6a8359443852357a1d889803958a647))
* retire codebase-audit agent 98 (tests-without-assertions) ([#1485](https://github.com/Aureliolo/synthorg/issues/1485)) ([d8b1b60](https://github.com/Aureliolo/synthorg/commit/d8b1b60904d403afc52713decd2401ef44121f80))
* set Renovate cadence to weekly Saturday morning ([#1540](https://github.com/Aureliolo/synthorg/issues/1540)) ([e012abe](https://github.com/Aureliolo/synthorg/commit/e012abeac3dade51402a869304d80c49c3f4c564))
* **skill:** add worktree launch subcommand + incremental plan guidance ([#1541](https://github.com/Aureliolo/synthorg/issues/1541)) ([70b8706](https://github.com/Aureliolo/synthorg/commit/70b8706d5f3f2e6611f9507b68d801e256699145))
* **skill:** worktree setup auto-launches wt tabs + inlines prompts for copy-paste ([#1547](https://github.com/Aureliolo/synthorg/issues/1547)) ([058ad6b](https://github.com/Aureliolo/synthorg/commit/058ad6b24e42facb1b73a47d085a4a630b10b682))
* supply chain hardening -- remove GPL from cli/go.mod + least-privilege Actions (SUP-1) ([#1499](https://github.com/Aureliolo/synthorg/issues/1499)) ([1040d05](https://github.com/Aureliolo/synthorg/commit/1040d051ed7c119081329de56dda233477ae353c))
* Update arigaio/atlas:latest-community-distroless Docker digest to a154831 ([#1508](https://github.com/Aureliolo/synthorg/issues/1508)) ([9e493c9](https://github.com/Aureliolo/synthorg/commit/9e493c97eeac1bf14e30f65b29dd99fab77e17f5))
* Update dependency goreleaser/goreleaser to v2.15.4 ([#1509](https://github.com/Aureliolo/synthorg/issues/1509)) ([9cbeaf7](https://github.com/Aureliolo/synthorg/commit/9cbeaf792e5e5766041c996b61efa2f50bd927af))
* Update module golang.org/x/vuln/cmd/govulncheck to v1.3.0 ([#1548](https://github.com/Aureliolo/synthorg/issues/1548)) ([73c6b39](https://github.com/Aureliolo/synthorg/commit/73c6b391abb839e37f0cad3d6306eb7c64ce9fb7))
* Update Python dependencies ([#1483](https://github.com/Aureliolo/synthorg/issues/1483)) ([2833956](https://github.com/Aureliolo/synthorg/commit/2833956520251e37629249ccb1f20520a40863c4))

## [0.7.1](https://github.com/Aureliolo/synthorg/compare/v0.7.0...v0.7.1) (2026-04-20)


### Features

* **budget:** persist currency on every cost row + Postgres/SQLite parity slice ([#1454](https://github.com/Aureliolo/synthorg/issues/1454)) ([3f2b9f7](https://github.com/Aureliolo/synthorg/commit/3f2b9f7e0b55b6df5281d64c79f6fec1f7b26424))
* **telemetry:** deployment environment tagging + Docker /info enrichment + silence Logfire introspection ([#1459](https://github.com/Aureliolo/synthorg/issues/1459)) ([7981bf6](https://github.com/Aureliolo/synthorg/commit/7981bf66b3c714e0903870221fd14945cbeeea4c))
* wire project telemetry, live NATS health probe, web healthcheck ([#1453](https://github.com/Aureliolo/synthorg/issues/1453)) ([eeb9492](https://github.com/Aureliolo/synthorg/commit/eeb9492e5e23d4bfe3d371ae3b3bd0cef3cf87d9))
* **wizard:** fix setup-wizard UX issues and stale-cookie recovery ([#1463](https://github.com/Aureliolo/synthorg/issues/1463)) ([6ff9874](https://github.com/Aureliolo/synthorg/commit/6ff98746cbadd3776db9962efa83f4b7ad20afc7))


### Bug Fixes

* **docker:** drop ':latest' default on backend BASE_IMAGE ARG ([#1465](https://github.com/Aureliolo/synthorg/issues/1465)) ([254f043](https://github.com/Aureliolo/synthorg/commit/254f043f68a5e255c1bfac07269cfc00bfa66ba2)), closes [#1464](https://github.com/Aureliolo/synthorg/issues/1464)
* split remaining TypeScript files + full verification ([#1481](https://github.com/Aureliolo/synthorg/issues/1481)) ([4338a74](https://github.com/Aureliolo/synthorg/commit/4338a748be6a35b31e6e64837012111005d75a2f))
* **web:** mark axios request interceptor synchronous + ratchet async-leak ceiling 70→66 ([#1480](https://github.com/Aureliolo/synthorg/issues/1480)) ([3cddefe](https://github.com/Aureliolo/synthorg/commit/3cddefe083a34bc2a4d68aa251f0572e59fc6277))


### Refactoring

* split oversize Python files + Go functions to comply with size limits ([#1473](https://github.com/Aureliolo/synthorg/issues/1473)) ([5c17c76](https://github.com/Aureliolo/synthorg/commit/5c17c762ea3cbdf372dc442b8568fbeb8e6e69a8))


### Tests

* **web:** ratchet async-leak ceiling to 50 and close [#1467](https://github.com/Aureliolo/synthorg/issues/1467) with design note ([#1469](https://github.com/Aureliolo/synthorg/issues/1469)) ([8405f99](https://github.com/Aureliolo/synthorg/commit/8405f9960f48a9349f018f90a8f14168dc85b9af))


### CI/CD

* fix DAST POSTGRES_PASSWORD + cosign install flake retry ([#1479](https://github.com/Aureliolo/synthorg/issues/1479)) ([21443f3](https://github.com/Aureliolo/synthorg/commit/21443f35509ddbd9b2c660e522c43bd769c5cf46))
* Update CI tool dependencies ([#1476](https://github.com/Aureliolo/synthorg/issues/1476)) ([eaf7e91](https://github.com/Aureliolo/synthorg/commit/eaf7e9141949e9e18e66c4c6ebd1c707af9a709a))


### Maintenance

* **ci:** audit PR-gated workflows + fix 282 web async leaks ([#1458](https://github.com/Aureliolo/synthorg/issues/1458)) ([6eb381a](https://github.com/Aureliolo/synthorg/commit/6eb381afae19a2d69f768f40db2bdf7aff9b2b81))
* consolidate Renovate lock-file PRs and delete orphan root package-lock.json ([#1482](https://github.com/Aureliolo/synthorg/issues/1482)) ([5b22810](https://github.com/Aureliolo/synthorg/commit/5b22810b23f22801701626284930af31dcd5838b))
* Lock file maintenance ([#1478](https://github.com/Aureliolo/synthorg/issues/1478)) ([7e69dce](https://github.com/Aureliolo/synthorg/commit/7e69dceb7bfca0a60ad7c5ae6b6ca0bdde656a11))
* **persistence:** consolidate 6 SQL subsystems, squash Atlas history, add boundary enforcement ([#1461](https://github.com/Aureliolo/synthorg/issues/1461)) ([28ab58a](https://github.com/Aureliolo/synthorg/commit/28ab58a18e7e94103510dd68fe3ad1ce96a9382d))
* skills audit, async-leak gate hardening, web HTTP adapter design spec ([#1471](https://github.com/Aureliolo/synthorg/issues/1471)) ([f94ed4d](https://github.com/Aureliolo/synthorg/commit/f94ed4dfaf33967aad5ee9c2c19f43970292b61a))
* Update Container dependencies ([#1474](https://github.com/Aureliolo/synthorg/issues/1474)) ([7c56453](https://github.com/Aureliolo/synthorg/commit/7c5645301ddd5903ddce4958bc2f3db34fc09ca8))
* Update Python dependencies ([#1451](https://github.com/Aureliolo/synthorg/issues/1451)) ([188b900](https://github.com/Aureliolo/synthorg/commit/188b900dff82feb165b2b3771b6703ec0d295e4b))

## [0.7.0](https://github.com/Aureliolo/synthorg/compare/v0.6.9...v0.7.0) (2026-04-18)


### Features

* API hardening -- error handling + per-op rate limits + escalation queue ([#1444](https://github.com/Aureliolo/synthorg/issues/1444)) ([b6558ae](https://github.com/Aureliolo/synthorg/commit/b6558aebad13b4f3c509d983be47a3375db9816c))
* implement engine quality, shadow evaluation, and applier dry-run ([#1383](https://github.com/Aureliolo/synthorg/issues/1383), [#1382](https://github.com/Aureliolo/synthorg/issues/1382), [#1381](https://github.com/Aureliolo/synthorg/issues/1381)) ([#1425](https://github.com/Aureliolo/synthorg/issues/1425)) ([e15c55c](https://github.com/Aureliolo/synthorg/commit/e15c55c10344bbe112fbe73b196a2e325930faf9))
* observability hardening -- logging, metrics, TSA, OTLP traces, tracked fire-and-forget tasks ([#1436](https://github.com/Aureliolo/synthorg/issues/1436)) ([64edcdf](https://github.com/Aureliolo/synthorg/commit/64edcdf45199068d5ca65507f48a16d218269a4a))
* **settings:** wire ~40 hardcoded values to SettingsService + ConfigResolver ([#1440](https://github.com/Aureliolo/synthorg/issues/1440)) ([28e3738](https://github.com/Aureliolo/synthorg/commit/28e373875d79a4de7a5fa99a991256ed8b7d6c31))
* split fine-tune image into -gpu and -cpu variants + free CI runner disk ([#1442](https://github.com/Aureliolo/synthorg/issues/1442)) ([cf8744e](https://github.com/Aureliolo/synthorg/commit/cf8744e249fcddf82ec2c79ed16d7fe0710aa978))


### Bug Fixes

* **cli,backend:** postgres mode actually works end-to-end ([#1427](https://github.com/Aureliolo/synthorg/issues/1427)) ([9501b1c](https://github.com/Aureliolo/synthorg/commit/9501b1c7e5b8f9dde82297e2e1682f693e7a9e33))
* **cli:** tunables + error wrapping + workerCmd wiring ([#1424](https://github.com/Aureliolo/synthorg/issues/1424)) ([7f3e942](https://github.com/Aureliolo/synthorg/commit/7f3e94221cd29f498e1df5ae905c301132713149))
* correct volume ownership for DHI postgres and nats containers ([#1426](https://github.com/Aureliolo/synthorg/issues/1426)) ([496babf](https://github.com/Aureliolo/synthorg/commit/496babf799c18170525030a6e1a32e941be29b01))
* dashboard quality pass (locale, DTO parity, a11y, training UI) ([#1433](https://github.com/Aureliolo/synthorg/issues/1433)) ([76c3937](https://github.com/Aureliolo/synthorg/commit/76c39376e9a9cf115a8bedfe508276976e14ff1a)), closes [#1402](https://github.com/Aureliolo/synthorg/issues/1402) [#1386](https://github.com/Aureliolo/synthorg/issues/1386) [#1389](https://github.com/Aureliolo/synthorg/issues/1389) [#1394](https://github.com/Aureliolo/synthorg/issues/1394)
* defaults cleanup -- hardcoded network values + regional bias + Postgres SessionStore ([#1445](https://github.com/Aureliolo/synthorg/issues/1445)) ([b9378aa](https://github.com/Aureliolo/synthorg/commit/b9378aa114c21ca2800ed7bebb902bc4bad94939)), closes [#1399](https://github.com/Aureliolo/synthorg/issues/1399) [#1437](https://github.com/Aureliolo/synthorg/issues/1437)
* fresh-install backend restart loop + encrypted_postgres secret backend + CLI encryption toggle ([#1438](https://github.com/Aureliolo/synthorg/issues/1438)) ([51a2615](https://github.com/Aureliolo/synthorg/commit/51a2615f77dc044a7595c196081d2a1b9bf2f676))
* frontend cleanup batch (dead code, stores, empty state, currency, chart tokens) ([#1422](https://github.com/Aureliolo/synthorg/issues/1422)) ([1562f39](https://github.com/Aureliolo/synthorg/commit/1562f392f4450774a36ad5e33a98c1c49a14b8ec)), closes [#1396](https://github.com/Aureliolo/synthorg/issues/1396) [#1387](https://github.com/Aureliolo/synthorg/issues/1387) [#1406](https://github.com/Aureliolo/synthorg/issues/1406) [#1409](https://github.com/Aureliolo/synthorg/issues/1409) [#1388](https://github.com/Aureliolo/synthorg/issues/1388)
* kill stubs across engine/meta/security/tools/communication ([#1434](https://github.com/Aureliolo/synthorg/issues/1434)) ([cf38bea](https://github.com/Aureliolo/synthorg/commit/cf38beaf9c2c6e810e204a6bce33c1899089613d)), closes [#1408](https://github.com/Aureliolo/synthorg/issues/1408) [#1392](https://github.com/Aureliolo/synthorg/issues/1392) [#1414](https://github.com/Aureliolo/synthorg/issues/1414) [#1413](https://github.com/Aureliolo/synthorg/issues/1413) [#1419](https://github.com/Aureliolo/synthorg/issues/1419)
* model/convention hygiene across Python src/ (5 issues) ([#1423](https://github.com/Aureliolo/synthorg/issues/1423)) ([5995ca9](https://github.com/Aureliolo/synthorg/commit/5995ca92d2599945de02fd22db900796c541f0f4)), closes [#1390](https://github.com/Aureliolo/synthorg/issues/1390) [#1397](https://github.com/Aureliolo/synthorg/issues/1397) [#1401](https://github.com/Aureliolo/synthorg/issues/1401) [#1403](https://github.com/Aureliolo/synthorg/issues/1403) [#1407](https://github.com/Aureliolo/synthorg/issues/1407)
* real rollout observation + Welch's t-test + rollback dispatch + client factory ([#1441](https://github.com/Aureliolo/synthorg/issues/1441)) ([3c4315e](https://github.com/Aureliolo/synthorg/commit/3c4315e3ab72d4f437a2c21a9e08ae2a91b75953))


### CI/CD

* Update CI tool dependencies ([#1432](https://github.com/Aureliolo/synthorg/issues/1432)) ([ec93737](https://github.com/Aureliolo/synthorg/commit/ec93737f295091f664a3fa2aea83cc8c4c05fcbf))


### Maintenance

* block opentelemetry-* &gt;=1.41.0 in Renovate while logfire pins &lt;1.41.0 ([#1450](https://github.com/Aureliolo/synthorg/issues/1450)) ([15b95ce](https://github.com/Aureliolo/synthorg/commit/15b95cee73febc2e892bb4d43bb9c0068d2d5a1a))
* expand audit skill to 75 agents + worktree decision protocol ([#1421](https://github.com/Aureliolo/synthorg/issues/1421)) ([59796ca](https://github.com/Aureliolo/synthorg/commit/59796ca3d6f068675dc0cb7fc1528a962b0bfb30))
* rewrite /codebase-audit skill with 58-agent roster and file-based output ([#1378](https://github.com/Aureliolo/synthorg/issues/1378)) ([2696e3c](https://github.com/Aureliolo/synthorg/commit/2696e3cb81b00279f26d8a4dbfa96b3a1467d266))
* Update dependency mem0ai to v2 ([#1435](https://github.com/Aureliolo/synthorg/issues/1435)) ([9ef8140](https://github.com/Aureliolo/synthorg/commit/9ef814053b92a71bb8bc3310342cec443c863e52))
* Update dhi.io/nats:2.12-debian13 Docker digest to ec28201 ([#1447](https://github.com/Aureliolo/synthorg/issues/1447)) ([8604869](https://github.com/Aureliolo/synthorg/commit/86048698afecd3f61b3eb94bcd32e2eee150bcde))
* Update golang:1.26-alpine Docker digest to f853308 ([#1428](https://github.com/Aureliolo/synthorg/issues/1428)) ([f8aee98](https://github.com/Aureliolo/synthorg/commit/f8aee98075284e8abd2bceb2c929eea5cdfa54a0))
* Update module charm.land/bubbletea/v2 to v2.0.6 ([#1429](https://github.com/Aureliolo/synthorg/issues/1429)) ([e04a70e](https://github.com/Aureliolo/synthorg/commit/e04a70e1966a75d45b5211bbce0d9c8bcb629117))
* Update Python dependencies to v0.15.11 ([#1430](https://github.com/Aureliolo/synthorg/issues/1430)) ([4242550](https://github.com/Aureliolo/synthorg/commit/424255095de2856d91aa8f85490158c76656490b))
* Update Web dependencies ([#1431](https://github.com/Aureliolo/synthorg/issues/1431)) ([9c48619](https://github.com/Aureliolo/synthorg/commit/9c4861940ad063aa39bd2feb04943ad9ed82400d))
* Update Web dependencies ([#1448](https://github.com/Aureliolo/synthorg/issues/1448)) ([40bf3d3](https://github.com/Aureliolo/synthorg/commit/40bf3d3a0b664b87f91c74cb52d2b981f127f10d))

## [0.6.9](https://github.com/Aureliolo/synthorg/compare/v0.6.8...v0.6.9) (2026-04-16)


### Features

* A/B testing rollout strategy for self-improvement proposals ([#1352](https://github.com/Aureliolo/synthorg/issues/1352)) ([e68d9ce](https://github.com/Aureliolo/synthorg/commit/e68d9ce7faa1682b818ac9dec598769bc7246b09)), closes [#1344](https://github.com/Aureliolo/synthorg/issues/1344)
* Chief of Staff advanced capabilities (memory, proactive alerts, learning) ([#1356](https://github.com/Aureliolo/synthorg/issues/1356)) ([fee5771](https://github.com/Aureliolo/synthorg/commit/fee5771ad0fa37235a7d90ce3a61373d8bd24f5f)), closes [#1342](https://github.com/Aureliolo/synthorg/issues/1342)
* CLI UX overhaul + DHI image verification + compose hardening ([#1359](https://github.com/Aureliolo/synthorg/issues/1359)) ([4e8f0a3](https://github.com/Aureliolo/synthorg/commit/4e8f0a39af3d82a3806ec4726a3c2474a5684441))
* cross-deployment analytics for self-improvement patterns ([#1363](https://github.com/Aureliolo/synthorg/issues/1363)) ([d1f6198](https://github.com/Aureliolo/synthorg/commit/d1f61985a4f78b5ae8806a03eb7a5fddacbfd414)), closes [#1341](https://github.com/Aureliolo/synthorg/issues/1341)
* custom rule authoring UI for self-improvement ([#1355](https://github.com/Aureliolo/synthorg/issues/1355)) ([859511b](https://github.com/Aureliolo/synthorg/commit/859511b7de91152d84a95438dc304e1b495d4ecb)), closes [#1343](https://github.com/Aureliolo/synthorg/issues/1343)
* expose full SynthOrg API as MCP server with capability-based scoping ([#1353](https://github.com/Aureliolo/synthorg/issues/1353)) ([750d1cb](https://github.com/Aureliolo/synthorg/commit/750d1cb10f9c979f0c21469ffd0891bd80232077)), closes [#1339](https://github.com/Aureliolo/synthorg/issues/1339)
* product-level self-improvement (framework code modification proposals) ([#1357](https://github.com/Aureliolo/synthorg/issues/1357)) ([6fec613](https://github.com/Aureliolo/synthorg/commit/6fec61380ecef6ea5a09c0a49c4ecf970d39f99e)), closes [#1340](https://github.com/Aureliolo/synthorg/issues/1340)
* self-improvement meta-loop hardening + dashboard completion ([#1376](https://github.com/Aureliolo/synthorg/issues/1376)) ([5824442](https://github.com/Aureliolo/synthorg/commit/582444253af251d53e9ec9eb05644bae6368e64c)), closes [#1375](https://github.com/Aureliolo/synthorg/issues/1375) [#1213](https://github.com/Aureliolo/synthorg/issues/1213)


### Bug Fixes

* `[A-Z_]+` -&gt; `[A-Z0-9_]+` ([2b02524](https://github.com/Aureliolo/synthorg/commit/2b02524419d92d0a2b4999765d962c9586e00af1))
* include digits in Renovate VERSION regex to detect D2_VERSION ([#1366](https://github.com/Aureliolo/synthorg/issues/1366)) ([2b02524](https://github.com/Aureliolo/synthorg/commit/2b02524419d92d0a2b4999765d962c9586e00af1))
* resolve Renovate DHI registry lookup failures ([#1365](https://github.com/Aureliolo/synthorg/issues/1365)) ([57c14dd](https://github.com/Aureliolo/synthorg/commit/57c14ddbe2a67220d7d683f3c8d58539817c3aae))


### CI/CD

* Update github/codeql-action action to v4.35.2 ([#1362](https://github.com/Aureliolo/synthorg/issues/1362)) ([732b446](https://github.com/Aureliolo/synthorg/commit/732b446b2868a6ba9ce470c86e6a45414cf0a0b5))


### Maintenance

* add Renovate best practices (configMigration, minimumReleaseAge, security bypass) ([#1371](https://github.com/Aureliolo/synthorg/issues/1371)) ([665e761](https://github.com/Aureliolo/synthorg/commit/665e761feb8b3f985800f6e88fde11ecf2baa516))
* **config:** migrate Renovate config ([#1372](https://github.com/Aureliolo/synthorg/issues/1372)) ([c26e465](https://github.com/Aureliolo/synthorg/commit/c26e4657c1991a3f3b513b928483824752b9cd91))
* consolidate Renovate PR groups to eliminate overlaps ([#1374](https://github.com/Aureliolo/synthorg/issues/1374)) ([fa4e828](https://github.com/Aureliolo/synthorg/commit/fa4e8281e9bc7a45762331cc54c610fbffa31e15))
* Lock file maintenance ([#1369](https://github.com/Aureliolo/synthorg/issues/1369)) ([cf32a25](https://github.com/Aureliolo/synthorg/commit/cf32a25137cee8b1323a5ae22b3e1fd17183c1a1))
* Lock file maintenance ([#1370](https://github.com/Aureliolo/synthorg/issues/1370)) ([0ed0a40](https://github.com/Aureliolo/synthorg/commit/0ed0a40aff315245c2fab86cfc7c99f75dbd0862))
* Lock file maintenance ([#1377](https://github.com/Aureliolo/synthorg/issues/1377)) ([5285332](https://github.com/Aureliolo/synthorg/commit/5285332f8fc56c9d834a9745ffadf56b41af1f4b))
* remove redundant Renovate security/releaseAge rules ([#1373](https://github.com/Aureliolo/synthorg/issues/1373)) ([7948788](https://github.com/Aureliolo/synthorg/commit/79487888e0da448f47efd3796d7b3afd5fa28a52))
* Update Container dependencies ([#1358](https://github.com/Aureliolo/synthorg/issues/1358)) ([7378d5b](https://github.com/Aureliolo/synthorg/commit/7378d5b8c5b87a45817d4c23fd15ad4eab3e6adc))
* Update Python dependencies ([#1360](https://github.com/Aureliolo/synthorg/issues/1360)) ([d71eadb](https://github.com/Aureliolo/synthorg/commit/d71eadb81d5e1e2b9c6a17402e4a6a2faf695fc5))
* Update Web dependencies ([#1361](https://github.com/Aureliolo/synthorg/issues/1361)) ([58fa19e](https://github.com/Aureliolo/synthorg/commit/58fa19e6b9e3ff39c51740d06b08235c956b40a2))

## [0.6.8](https://github.com/Aureliolo/synthorg/compare/v0.6.7...v0.6.8) (2026-04-15)


### Features

* A2A external gateway implementation ([#1335](https://github.com/Aureliolo/synthorg/issues/1335)) ([d96c712](https://github.com/Aureliolo/synthorg/commit/d96c712eebc65985e42f3551987ba63292722298)), closes [#1164](https://github.com/Aureliolo/synthorg/issues/1164)
* async task protocol with citation tracking + cross-agent skill pool ([#1312](https://github.com/Aureliolo/synthorg/issues/1312)) ([13125fd](https://github.com/Aureliolo/synthorg/commit/13125fd054f199dccb4f3c5c84029abe36bbce81)), closes [#1264](https://github.com/Aureliolo/synthorg/issues/1264) [#1246](https://github.com/Aureliolo/synthorg/issues/1246)
* **communication:** AG-UI event stream + Evidence Package schema ([#1294](https://github.com/Aureliolo/synthorg/issues/1294)) ([3baa1b9](https://github.com/Aureliolo/synthorg/commit/3baa1b917b2382edd08f354eefb74093dab3dd56)), closes [#1263](https://github.com/Aureliolo/synthorg/issues/1263)
* container supply chain alignment -- full image parity, fine-tune image, sandbox lifecycle, CLI UX ([#1337](https://github.com/Aureliolo/synthorg/issues/1337)) ([01a5709](https://github.com/Aureliolo/synthorg/commit/01a57090a2e259e8ef7fe34622f4f95c5785e172)), closes [#1336](https://github.com/Aureliolo/synthorg/issues/1336)
* **engine:** brain/hands/session decoupling + stateless session recovery ([#1292](https://github.com/Aureliolo/synthorg/issues/1292)) ([7a484d8](https://github.com/Aureliolo/synthorg/commit/7a484d86db7a90fe8dd98bc2aa346810f84f6c1e)), closes [#1261](https://github.com/Aureliolo/synthorg/issues/1261)
* **hr/eval:** close trace-eval-pattern-fix loop with behavior tagging ([#1301](https://github.com/Aureliolo/synthorg/issues/1301)) ([933c406](https://github.com/Aureliolo/synthorg/commit/933c406052f4c1c2f6cf4947cbfaac7bf8c48fac)), closes [#1265](https://github.com/Aureliolo/synthorg/issues/1265)
* implement self-improving company meta-loop ([#255](https://github.com/Aureliolo/synthorg/issues/255)) ([#1345](https://github.com/Aureliolo/synthorg/issues/1345)) ([d1bb15d](https://github.com/Aureliolo/synthorg/commit/d1bb15d4c0464d8e15cbf680c68db94f17f2255f))
* persist training plans and wire TrainingService into AppState ([#1293](https://github.com/Aureliolo/synthorg/issues/1293)) ([da9a3e2](https://github.com/Aureliolo/synthorg/commit/da9a3e26927b2d4eb369fcb44d4b895a93a8c1ed)), closes [#1242](https://github.com/Aureliolo/synthorg/issues/1242)
* progressive L1/L2/L3 tool disclosure ([#1245](https://github.com/Aureliolo/synthorg/issues/1245)) ([#1300](https://github.com/Aureliolo/synthorg/issues/1300)) ([2f60736](https://github.com/Aureliolo/synthorg/commit/2f60736f502edb17e2727bcfed39f212b4cf0cee))
* **security:** policy engine + OWASP coverage audit + threat model + quantum-safe audit trail ([#1314](https://github.com/Aureliolo/synthorg/issues/1314)) ([a0966a6](https://github.com/Aureliolo/synthorg/commit/a0966a668449f41419834776717569bd8b8f012c)), closes [#1268](https://github.com/Aureliolo/synthorg/issues/1268)
* ship sandbox/sidecar container logs to observability stack + NATS 2.12 review ([#1313](https://github.com/Aureliolo/synthorg/issues/1313)) ([9e5f16a](https://github.com/Aureliolo/synthorg/commit/9e5f16a5d79fb3f7f58a3f5e1728911fe73beed0)), closes [#1303](https://github.com/Aureliolo/synthorg/issues/1303) [#1298](https://github.com/Aureliolo/synthorg/issues/1298)
* sidecar network proxy for fully rootless sandbox ([#1302](https://github.com/Aureliolo/synthorg/issues/1302)) ([ddcf815](https://github.com/Aureliolo/synthorg/commit/ddcf8155d4014be0f9e2d65ff08b2e6877bcb458)), closes [#1287](https://github.com/Aureliolo/synthorg/issues/1287)


### Bug Fixes

* **ci:** prevent transitive skip propagation in Docker workflow ([#1296](https://github.com/Aureliolo/synthorg/issues/1296)) ([2313326](https://github.com/Aureliolo/synthorg/commit/231332604080fd3a35e513376276646560dabfb1))
* **deps:** update dependency litellm to v1.83.7 ([#1318](https://github.com/Aureliolo/synthorg/issues/1318)) ([1bab6da](https://github.com/Aureliolo/synthorg/commit/1bab6da8d5d718ef1781ef6832a8810db38ed2bb))
* **deps:** update dependency packaging to v26.1 ([#1349](https://github.com/Aureliolo/synthorg/issues/1349)) ([66f2084](https://github.com/Aureliolo/synthorg/commit/66f20843f539026c55a47c4b21b597c3944e9775))
* **deps:** update dependency sentence-transformers to v5.4.1 ([#1333](https://github.com/Aureliolo/synthorg/issues/1333)) ([7ce917d](https://github.com/Aureliolo/synthorg/commit/7ce917da50150dea17b7e156e1e5229290ae31c5))
* migrate deprecated Renovate config to v43+ syntax ([#1305](https://github.com/Aureliolo/synthorg/issues/1305)) ([62306d8](https://github.com/Aureliolo/synthorg/commit/62306d83b67f9c7ecea24572453354293a907f9c)), closes [#1304](https://github.com/Aureliolo/synthorg/issues/1304)
* use pep621 manager and enable lock file maintenance in Renovate ([#1351](https://github.com/Aureliolo/synthorg/issues/1351)) ([71af17c](https://github.com/Aureliolo/synthorg/commit/71af17c87d65b1e78df0cb20027742469ba0eed4))


### CI/CD

* harden Docker pipeline -- drop Grype, non-root sidecar, HTTP healthchecks, CIS enforcement ([#1350](https://github.com/Aureliolo/synthorg/issues/1350)) ([ec289c5](https://github.com/Aureliolo/synthorg/commit/ec289c51c5c945b586e84971090117cb202585c7))
* replace Dependabot with Renovate + CI adaptations ([#1299](https://github.com/Aureliolo/synthorg/issues/1299)) ([ef67700](https://github.com/Aureliolo/synthorg/commit/ef67700510e80ef4b0bd0b4bf0d9283acdb7dba8)), closes [#1291](https://github.com/Aureliolo/synthorg/issues/1291)
* Update CI tool dependencies ([#1320](https://github.com/Aureliolo/synthorg/issues/1320)) ([d0175ac](https://github.com/Aureliolo/synthorg/commit/d0175acf93d40663563d4b1f28bb35aab977f0cf))
* Update CI tool dependencies (major) ([#1322](https://github.com/Aureliolo/synthorg/issues/1322)) ([2a806b4](https://github.com/Aureliolo/synthorg/commit/2a806b48d8b752928596aea518f116fc91a8b59d))


### Maintenance

* bump follow-redirects from 1.15.11 to 1.16.0 in /web in the npm_and_yarn group across 1 directory ([#1323](https://github.com/Aureliolo/synthorg/issues/1323)) ([bf5c708](https://github.com/Aureliolo/synthorg/commit/bf5c708415c2088a8e3e8fea3b687febcb7a1c25))
* bump Renovate prHourlyLimit to 5 ([#1317](https://github.com/Aureliolo/synthorg/issues/1317)) ([59f2376](https://github.com/Aureliolo/synthorg/commit/59f2376cdc314ec2731ca50a585a60a55c132616))
* **deps:** update dependency hypothesis to v6.151.14 ([#1316](https://github.com/Aureliolo/synthorg/issues/1316)) ([3cfcbdc](https://github.com/Aureliolo/synthorg/commit/3cfcbdc6e15dbf208b18e318401529e227ac5c9d))
* **deps:** update dependency hypothesis to v6.152.0 ([#1338](https://github.com/Aureliolo/synthorg/issues/1338)) ([a847526](https://github.com/Aureliolo/synthorg/commit/a84752606605a8e8f5c8ab30f4533d595f79c813))
* **deps:** update dependency hypothesis to v6.152.1 ([#1348](https://github.com/Aureliolo/synthorg/issues/1348)) ([39627c2](https://github.com/Aureliolo/synthorg/commit/39627c24441772065caa99f3ab59d72461baacaf))
* **deps:** update dependency zensical to v0.0.33 ([#1329](https://github.com/Aureliolo/synthorg/issues/1329)) ([9b5c159](https://github.com/Aureliolo/synthorg/commit/9b5c1599ba64d1b399ba7f7d7864541773f7325f))
* disable Renovate pinning of requires-python ([#1327](https://github.com/Aureliolo/synthorg/issues/1327)) ([e37bb6c](https://github.com/Aureliolo/synthorg/commit/e37bb6cfa0bf919393305fbe8c9b367ad4b9148f))
* enable Renovate pre-commit manager ([#1307](https://github.com/Aureliolo/synthorg/issues/1307)) ([2d99cad](https://github.com/Aureliolo/synthorg/commit/2d99cad44b55b30da50d2a2b0f76e36567ecd8fb))
* ignore cli/testdata/ in Renovate (golden files, not real configs) ([#1330](https://github.com/Aureliolo/synthorg/issues/1330)) ([7ca3c8e](https://github.com/Aureliolo/synthorg/commit/7ca3c8e54003d6cf9e73b44a8c99470b2f043098))
* major dependency upgrades (Charm v2 + framer-motion to motion) ([#1332](https://github.com/Aureliolo/synthorg/issues/1332)) ([66b1e5a](https://github.com/Aureliolo/synthorg/commit/66b1e5ab45ef88a7bfbb3dce617c3a30da4dc7e7))
* Pin dependencies ([#1310](https://github.com/Aureliolo/synthorg/issues/1310)) ([63cddd5](https://github.com/Aureliolo/synthorg/commit/63cddd5579c69abaca8ca47501f433e4b7539465))
* Update CLI dependencies ([#1315](https://github.com/Aureliolo/synthorg/issues/1315)) ([479f9e2](https://github.com/Aureliolo/synthorg/commit/479f9e2f527d524fcd419b558fd5a0d97b2b7217))
* Update Container dependencies ([#1319](https://github.com/Aureliolo/synthorg/issues/1319)) ([eb4d1a9](https://github.com/Aureliolo/synthorg/commit/eb4d1a9a35370391699ce46f55a8ee3a79540e54))
* Update dependency goreleaser/goreleaser to v2.15.3 ([#1346](https://github.com/Aureliolo/synthorg/issues/1346)) ([da874da](https://github.com/Aureliolo/synthorg/commit/da874da8944062d017ac45b749ce3de96ffe56e0))
* Update nats Docker tag to v2.12.7 ([#1347](https://github.com/Aureliolo/synthorg/issues/1347)) ([422b554](https://github.com/Aureliolo/synthorg/commit/422b5542c461cd1bbd8b38f86c247081272c0ca2))
* Update Web dependencies ([#1324](https://github.com/Aureliolo/synthorg/issues/1324)) ([d1b0183](https://github.com/Aureliolo/synthorg/commit/d1b018326c0fae801856419f573c25bd0da203aa))
* **web:** adopt Base UI Drawer + feat: NATS 2.11/2.12 TTL and batch publishes ([#1334](https://github.com/Aureliolo/synthorg/issues/1334)) ([f2afde1](https://github.com/Aureliolo/synthorg/commit/f2afde174c741c05e86c4083431fabca4e32a539)), closes [#1326](https://github.com/Aureliolo/synthorg/issues/1326) [#1308](https://github.com/Aureliolo/synthorg/issues/1308)

## [0.6.7](https://github.com/Aureliolo/synthorg/compare/v0.6.6...v0.6.7) (2026-04-13)


### Features

* **engine:** introduce middleware layer + coordination middleware split ([#1276](https://github.com/Aureliolo/synthorg/issues/1276)) ([4353e78](https://github.com/Aureliolo/synthorg/commit/4353e781fc09a7c06ce67e8dbbbbef9856fc600e))
* **engine:** verification stage node type + calibrated rubric + criteria decomposition ([#1286](https://github.com/Aureliolo/synthorg/issues/1286)) ([76e308f](https://github.com/Aureliolo/synthorg/commit/76e308fec7cebc1fb112b9fb91cbb771bf516fda)), closes [#1262](https://github.com/Aureliolo/synthorg/issues/1262)
* implement agent evolution and improvement over time ([#1229](https://github.com/Aureliolo/synthorg/issues/1229)) ([aad186f](https://github.com/Aureliolo/synthorg/commit/aad186f1f296aa8d6f027650c7230caa28fe397f)), closes [#243](https://github.com/Aureliolo/synthorg/issues/243)
* implement dynamic company scaling ([#1235](https://github.com/Aureliolo/synthorg/issues/1235)) ([19f07dd](https://github.com/Aureliolo/synthorg/commit/19f07dd8459c5ad83f367c3753b08c7b0dbaac21))
* implement external service integration APIs ([#1240](https://github.com/Aureliolo/synthorg/issues/1240)) ([94e8343](https://github.com/Aureliolo/synthorg/commit/94e834381e9e3598f41c5eab3bcbe073a0576c83))
* implement semantic analysis detectors for error taxonomy ([#1233](https://github.com/Aureliolo/synthorg/issues/1233)) ([6e4eb6d](https://github.com/Aureliolo/synthorg/commit/6e4eb6d245a2ec6eb018230923d79e787a75cfb4)), closes [#228](https://github.com/Aureliolo/synthorg/issues/228)
* implement training mode for agent learning ([#249](https://github.com/Aureliolo/synthorg/issues/249)) ([#1232](https://github.com/Aureliolo/synthorg/issues/1232)) ([b9fbcfb](https://github.com/Aureliolo/synthorg/commit/b9fbcfb240fce65852adfbaac2b2b6e80abb2275))
* **memory:** hierarchical retriever + knowledge architect role + GEMS two-tier compressor ([#1275](https://github.com/Aureliolo/synthorg/issues/1275)) ([5495053](https://github.com/Aureliolo/synthorg/commit/5495053d02248c4b2905821fde44286ee93eff99)), closes [#1266](https://github.com/Aureliolo/synthorg/issues/1266)
* migrate container supply chain to Wolfi/apko and replace nginx with Caddy ([#1285](https://github.com/Aureliolo/synthorg/issues/1285)) ([4b9f00a](https://github.com/Aureliolo/synthorg/commit/4b9f00a3a98995fc56aac75921c77fff359294e9)), closes [#1267](https://github.com/Aureliolo/synthorg/issues/1267)
* Postgres production readiness -- DB atomic ops, JSONB analytics, CLI orchestration ([#1239](https://github.com/Aureliolo/synthorg/issues/1239)) ([4796ffb](https://github.com/Aureliolo/synthorg/commit/4796ffb3f564b98c7bcbb0f7c037f8c0e1dfb12f)), closes [#1216](https://github.com/Aureliolo/synthorg/issues/1216) [#1211](https://github.com/Aureliolo/synthorg/issues/1211) [#1210](https://github.com/Aureliolo/synthorg/issues/1210)
* **web:** integrations dashboard (Connections, OAuth Apps, MCP Catalog, tunnel) ([#1270](https://github.com/Aureliolo/synthorg/issues/1270)) ([1ee110c](https://github.com/Aureliolo/synthorg/commit/1ee110cb44e88b1aad7c5d5d1bc8d7da6251c16d))


### Bug Fixes

* **ci:** use scan-action for grype diagnostic output ([#1231](https://github.com/Aureliolo/synthorg/issues/1231)) ([18d7de9](https://github.com/Aureliolo/synthorg/commit/18d7de985fc61ae2bddab06f300c3b5dbaa70a5f))
* **cli:** mount docker.sock on backend, drop zombie sandbox service ([#1269](https://github.com/Aureliolo/synthorg/issues/1269)) ([c87afb5](https://github.com/Aureliolo/synthorg/commit/c87afb5f2e3582131fba8685c4b22737d413d0d7))
* subworkflow follow-up fixes + dashboard UI + integration tests ([#1223](https://github.com/Aureliolo/synthorg/issues/1223), [#1218](https://github.com/Aureliolo/synthorg/issues/1218)) ([#1230](https://github.com/Aureliolo/synthorg/issues/1230)) ([36e3062](https://github.com/Aureliolo/synthorg/commit/36e3062128d31464c9497fa19b6494202cb2bfe2))


### Performance

* reusable test_client fixture (195s to 81s, 58% faster) ([#1277](https://github.com/Aureliolo/synthorg/issues/1277)) ([68aa9c2](https://github.com/Aureliolo/synthorg/commit/68aa9c2b8ee3a6226eb78eb9299c3892f71df190)), closes [#1272](https://github.com/Aureliolo/synthorg/issues/1272)


### Refactoring

* evaluate nats-core + split bus/nats.py into focused modules ([#1228](https://github.com/Aureliolo/synthorg/issues/1228)) ([3bc57bf](https://github.com/Aureliolo/synthorg/commit/3bc57bfa7fba5d15aff2741a1acf96b46b909436)), closes [#1217](https://github.com/Aureliolo/synthorg/issues/1217) [#1221](https://github.com/Aureliolo/synthorg/issues/1221)
* persistence cleanup -- TestClient fix + migration squash strategy ([#1274](https://github.com/Aureliolo/synthorg/issues/1274)) ([95b684c](https://github.com/Aureliolo/synthorg/commit/95b684c1f4cc27be07564d4eb40ee523c96778dc))
* **persistence:** postgres followups + TimescaleDB hypertable support ([#1271](https://github.com/Aureliolo/synthorg/issues/1271)) ([be0cf09](https://github.com/Aureliolo/synthorg/commit/be0cf09f52e3aa86f98950e9ee7bf5dac2cef7e2))


### Documentation

* replace ASCII/Unicode box diagrams with Mermaid + D2 hybrid tooling ([#1234](https://github.com/Aureliolo/synthorg/issues/1234)) ([88ce189](https://github.com/Aureliolo/synthorg/commit/88ce189282a9369dfb16e690a5ff62994421f24b))
* S1 multi-agent architecture decision + 15-risk register ([#1259](https://github.com/Aureliolo/synthorg/issues/1259)) ([3d580f2](https://github.com/Aureliolo/synthorg/commit/3d580f2b8ea75dc93b66f8fc7ddb73c4822f4ce2)), closes [#1254](https://github.com/Aureliolo/synthorg/issues/1254)


### Tests

* audit + clean up unit test suite (regression guard, sleeps, hypothesis, resource leaks) ([#1273](https://github.com/Aureliolo/synthorg/issues/1273)) ([fddcb22](https://github.com/Aureliolo/synthorg/commit/fddcb222466bcb9dea3b5232bdcd4cb4b9c006b9)), closes [#1243](https://github.com/Aureliolo/synthorg/issues/1243)


### Maintenance

* bump @tanstack/react-query from 5.97.0 to 5.99.0 in /web in the all group ([#1283](https://github.com/Aureliolo/synthorg/issues/1283)) ([5acedda](https://github.com/Aureliolo/synthorg/commit/5acedda328762b6a4c4146cd0ae47b89252c2a0a))
* bump github.com/google/go-containerregistry from 0.21.4 to 0.21.5 in /cli in the all group ([#1282](https://github.com/Aureliolo/synthorg/issues/1282)) ([7260335](https://github.com/Aureliolo/synthorg/commit/72603355f8769b6e43677ad33836bafe9f603c30))
* bump https://github.com/commitizen-tools/commitizen from v4.13.9 to 4.13.10 in the all group across 1 directory ([#1281](https://github.com/Aureliolo/synthorg/issues/1281)) ([3a5fd65](https://github.com/Aureliolo/synthorg/commit/3a5fd65e83f295d1f2025bf10390a24f6cfbc415))
* bump the all group across 1 directory with 5 updates ([#1289](https://github.com/Aureliolo/synthorg/issues/1289)) ([1af1ba7](https://github.com/Aureliolo/synthorg/commit/1af1ba7741972cb4640ad891a7724d44bd00da4d))
* **ci:** audit and update GitHub Actions from v0.6.6 release ([#1225](https://github.com/Aureliolo/synthorg/issues/1225)) ([6251227](https://github.com/Aureliolo/synthorg/commit/6251227802a31cd75ed1290a79124ca6457dca7f)), closes [#1224](https://github.com/Aureliolo/synthorg/issues/1224)

## [0.6.6](https://github.com/Aureliolo/synthorg/compare/v0.6.5...v0.6.6) (2026-04-10)


### Features

* add agent pruning/dropout service ([#1126](https://github.com/Aureliolo/synthorg/issues/1126)) ([#1190](https://github.com/Aureliolo/synthorg/issues/1190)) ([0f216e8](https://github.com/Aureliolo/synthorg/commit/0f216e843b048623aa9840afa8f0449baace42f7))
* client simulation contracts and TaskStatus extension ([#1195](https://github.com/Aureliolo/synthorg/issues/1195)) ([56975c9](https://github.com/Aureliolo/synthorg/commit/56975c9cadec6c73e84d0951cf6c7a74d8bc0f82)), closes [#1169](https://github.com/Aureliolo/synthorg/issues/1169) [#1161](https://github.com/Aureliolo/synthorg/issues/1161)
* client simulation strategies, API, dashboard, and production integration ([#1219](https://github.com/Aureliolo/synthorg/issues/1219)) ([ae489a4](https://github.com/Aureliolo/synthorg/commit/ae489a4eb53f8ee11470c911ef96e758527a74f8)), closes [#1170](https://github.com/Aureliolo/synthorg/issues/1170) [#1171](https://github.com/Aureliolo/synthorg/issues/1171)
* distributed runtime -- NATS JetStream bus backend + task queue ([#1214](https://github.com/Aureliolo/synthorg/issues/1214)) ([2c62703](https://github.com/Aureliolo/synthorg/commit/2c627034fccc04b1e3e35fdeb08e56b73976c871))
* ontology integration layer, REST API, and dashboard ([#1166](https://github.com/Aureliolo/synthorg/issues/1166), [#1167](https://github.com/Aureliolo/synthorg/issues/1167)) ([#1197](https://github.com/Aureliolo/synthorg/issues/1197)) ([c96de07](https://github.com/Aureliolo/synthorg/commit/c96de0778f2ab9de99b1d11b3f8b23daaad6a304))
* opt-in anonymous product telemetry via pluggable backend ([#1200](https://github.com/Aureliolo/synthorg/issues/1200)) ([92997bc](https://github.com/Aureliolo/synthorg/commit/92997bce2239fc72641b5a4737d039c166ed996b)), closes [#1199](https://github.com/Aureliolo/synthorg/issues/1199)
* Parts-based message model + trendslop mitigation phase 2 ([#1196](https://github.com/Aureliolo/synthorg/issues/1196)) ([a5578e4](https://github.com/Aureliolo/synthorg/commit/a5578e45bdbcc3d284dfa1bc67df4bc3a6044b80)), closes [#1160](https://github.com/Aureliolo/synthorg/issues/1160) [#1158](https://github.com/Aureliolo/synthorg/issues/1158)
* postgres persistence backend ([#1215](https://github.com/Aureliolo/synthorg/issues/1215)) ([7fc849b](https://github.com/Aureliolo/synthorg/commit/7fc849bd1d78308d51931255db6839108aa9ef38))
* semantic ontology core subsystem -- models, protocol, backend, config, bootstrap ([#1192](https://github.com/Aureliolo/synthorg/issues/1192)) ([b331e4a](https://github.com/Aureliolo/synthorg/commit/b331e4a9b3971e9fb2374b31ade612f19af2c541)), closes [#1165](https://github.com/Aureliolo/synthorg/issues/1165)
* subworkflows -- nestable reusable workflow components ([#1012](https://github.com/Aureliolo/synthorg/issues/1012)) ([#1220](https://github.com/Aureliolo/synthorg/issues/1220)) ([ef1a41a](https://github.com/Aureliolo/synthorg/commit/ef1a41a99b599fef8da1c27c11e7ac2adf2a53a2))
* trendslop mitigation phase 1 -- strategy module core models, config, and prompt integration ([#1191](https://github.com/Aureliolo/synthorg/issues/1191)) ([4b83358](https://github.com/Aureliolo/synthorg/commit/4b833582bef6d70b2048d2d10bc39bcd53b08d4c)), closes [#1157](https://github.com/Aureliolo/synthorg/issues/1157)


### Bug Fixes

* block python pre-release tags in dependabot ignore rules ([#1209](https://github.com/Aureliolo/synthorg/issues/1209)) ([2b4b520](https://github.com/Aureliolo/synthorg/commit/2b4b52055ff1cce553027af08611b9bdc2661b8d))


### Documentation

* A2A gateway spec, skill enrichment, DelegationGuard gaps ([#1189](https://github.com/Aureliolo/synthorg/issues/1189)) ([408d6c7](https://github.com/Aureliolo/synthorg/commit/408d6c7b9892caa75b8d0fbf90be7aff78c677a4)), closes [#1159](https://github.com/Aureliolo/synthorg/issues/1159) [#1162](https://github.com/Aureliolo/synthorg/issues/1162) [#1163](https://github.com/Aureliolo/synthorg/issues/1163)


### CI/CD

* bump actions/github-script from 8.0.0 to 9.0.0 in the all group ([#1207](https://github.com/Aureliolo/synthorg/issues/1207)) ([8998b6d](https://github.com/Aureliolo/synthorg/commit/8998b6d452784d0bad9f6e760289d02f53edbb8b))
* bump wrangler from 4.81.0 to 4.81.1 in /.github in the all group ([#1205](https://github.com/Aureliolo/synthorg/issues/1205)) ([6aa4aba](https://github.com/Aureliolo/synthorg/commit/6aa4aba2085bb65ef46be996638ae373147bff85))


### Maintenance

* adopt Atlas declarative schema migrations with automatic squashing ([#1198](https://github.com/Aureliolo/synthorg/issues/1198)) ([a4f96b1](https://github.com/Aureliolo/synthorg/commit/a4f96b1c0e8fa28718cee1c21b4d5f90f9e9ddf1)), closes [#1194](https://github.com/Aureliolo/synthorg/issues/1194)
* bump https://github.com/astral-sh/ruff-pre-commit from v0.15.9 to 0.15.10 in the all group ([#1204](https://github.com/Aureliolo/synthorg/issues/1204)) ([6d65e0d](https://github.com/Aureliolo/synthorg/commit/6d65e0d5b42327d323d8537fccb9e6e1e22108c0))
* bump the all group in /web with 2 updates ([#1187](https://github.com/Aureliolo/synthorg/issues/1187)) ([617d375](https://github.com/Aureliolo/synthorg/commit/617d375455c8e57ccd0336e2837727322cc5cb6d))
* bump the all group in /web with 2 updates ([#1206](https://github.com/Aureliolo/synthorg/issues/1206)) ([0f7ddf2](https://github.com/Aureliolo/synthorg/commit/0f7ddf2928eb2b79e3ec7b66d5035af62de70349))
* bump vitest from 4.1.3 to 4.1.4 in /site in the all group ([#1186](https://github.com/Aureliolo/synthorg/issues/1186)) ([5929bbc](https://github.com/Aureliolo/synthorg/commit/5929bbc58ddf04c0306f653f8f3afc7b9b302f80))

## [0.6.5](https://github.com/Aureliolo/synthorg/compare/v0.6.4...v0.6.5) (2026-04-09)


### Features

* add control-plane API endpoints batch ([#1118](https://github.com/Aureliolo/synthorg/issues/1118), [#1119](https://github.com/Aureliolo/synthorg/issues/1119), [#1120](https://github.com/Aureliolo/synthorg/issues/1120), [#1121](https://github.com/Aureliolo/synthorg/issues/1121)) ([#1138](https://github.com/Aureliolo/synthorg/issues/1138)) ([af11f0a](https://github.com/Aureliolo/synthorg/commit/af11f0a91f599f04773b4531a323e67afe786a69))
* engine intelligence v2 -- trace enrichment, compaction, versioning eval ([#1139](https://github.com/Aureliolo/synthorg/issues/1139)) ([ed57dfa](https://github.com/Aureliolo/synthorg/commit/ed57dfa6eb0c1fe56a3db1d641f616fc310210ab)), closes [#1123](https://github.com/Aureliolo/synthorg/issues/1123) [#1125](https://github.com/Aureliolo/synthorg/issues/1125) [#1113](https://github.com/Aureliolo/synthorg/issues/1113)
* generalize versioning to VersionSnapshot[T] for all entity types ([#1155](https://github.com/Aureliolo/synthorg/issues/1155)) ([5f563ce](https://github.com/Aureliolo/synthorg/commit/5f563ced7a4e55e371f16dc24be0231c2f41aa15)), closes [#1131](https://github.com/Aureliolo/synthorg/issues/1131) [#1132](https://github.com/Aureliolo/synthorg/issues/1132) [#1133](https://github.com/Aureliolo/synthorg/issues/1133)
* implement auxiliary tool categories -- design, communication, analytics ([#1152](https://github.com/Aureliolo/synthorg/issues/1152)) ([b506ba4](https://github.com/Aureliolo/synthorg/commit/b506ba4c4edda273166fdb06bdbb99924c854c16))
* implement multi-project support -- engine orchestration ([#242](https://github.com/Aureliolo/synthorg/issues/242)) ([#1153](https://github.com/Aureliolo/synthorg/issues/1153)) ([74f1362](https://github.com/Aureliolo/synthorg/commit/74f1362d7fc29249b640d7ba802c298f76a5620e))
* implement SharedKnowledgeStore append-only + MVCC consistency model (Phase 1.5) ([#1134](https://github.com/Aureliolo/synthorg/issues/1134)) ([965d3a1](https://github.com/Aureliolo/synthorg/commit/965d3a1bfb85334a4a42c850d8d785e6a9cbd88e)), closes [#1130](https://github.com/Aureliolo/synthorg/issues/1130)
* implement shutdown strategies and SUSPENDED task status ([#1151](https://github.com/Aureliolo/synthorg/issues/1151)) ([6a0db11](https://github.com/Aureliolo/synthorg/commit/6a0db1110d1cd5b4517fce9ab59771c310fb4498))
* persistent cost aggregation for project-lifetime budgets ([#1173](https://github.com/Aureliolo/synthorg/issues/1173)) ([5c212c5](https://github.com/Aureliolo/synthorg/commit/5c212c5eb3ba45885ed58a2892bc07dde8c8be22)), closes [#1156](https://github.com/Aureliolo/synthorg/issues/1156)
* Prometheus /metrics endpoint and OTLP exporter ([#1122](https://github.com/Aureliolo/synthorg/issues/1122)) ([#1135](https://github.com/Aureliolo/synthorg/issues/1135)) ([aaeaae9](https://github.com/Aureliolo/synthorg/commit/aaeaae938285c68119aa507e4defd0f8efb06e10)), closes [#1124](https://github.com/Aureliolo/synthorg/issues/1124)
* Prometheus metrics -- daily budget %, per-agent cost, per-agent budget % ([#1154](https://github.com/Aureliolo/synthorg/issues/1154)) ([581c494](https://github.com/Aureliolo/synthorg/commit/581c4942f7c1c8b48efa8e17b3f8e3b833c4376f)), closes [#1148](https://github.com/Aureliolo/synthorg/issues/1148)


### Bug Fixes

* communication hardening -- meeting cooldown, circuit breaker backoff, debate fallback ([#1140](https://github.com/Aureliolo/synthorg/issues/1140)) ([fe82894](https://github.com/Aureliolo/synthorg/commit/fe82894feaadd6fde8c17e1b7a7c50c64fecb10d)), closes [#1115](https://github.com/Aureliolo/synthorg/issues/1115) [#1116](https://github.com/Aureliolo/synthorg/issues/1116) [#1117](https://github.com/Aureliolo/synthorg/issues/1117)


### CI/CD

* bump wrangler from 4.80.0 to 4.81.0 in /.github in the all group ([#1144](https://github.com/Aureliolo/synthorg/issues/1144)) ([b7c0945](https://github.com/Aureliolo/synthorg/commit/b7c0945b5a6c85ee6cb4dc4b9a15a3e82d7aecd3))


### Maintenance

* bump python from `6869258` to `5e59aae` in /docker/backend in the all group ([#1141](https://github.com/Aureliolo/synthorg/issues/1141)) ([01e99c2](https://github.com/Aureliolo/synthorg/commit/01e99c222f65994b5c292fdf3b345e57d670d1e7))
* bump python from `6869258` to `5e59aae` in /docker/sandbox in the all group ([#1143](https://github.com/Aureliolo/synthorg/issues/1143)) ([ea755bd](https://github.com/Aureliolo/synthorg/commit/ea755bda42930ece80c9a68de95ba8476ac7bb03))
* bump python from `6869258` to `5e59aae` in /docker/web in the all group ([#1142](https://github.com/Aureliolo/synthorg/issues/1142)) ([5416dd9](https://github.com/Aureliolo/synthorg/commit/5416dd949ae00a46c28783f141c753ffc7aa0977))
* bump the all group across 1 directory with 2 updates ([#1181](https://github.com/Aureliolo/synthorg/issues/1181)) ([d3d5adf](https://github.com/Aureliolo/synthorg/commit/d3d5adfb0c96315a39e53720a9078577891858ca))
* bump the all group across 1 directory with 3 updates ([#1146](https://github.com/Aureliolo/synthorg/issues/1146)) ([c609e6c](https://github.com/Aureliolo/synthorg/commit/c609e6c92bec81284ef492c5d39cbdc3bd7aac9f))
* bump the all group in /cli with 2 updates ([#1177](https://github.com/Aureliolo/synthorg/issues/1177)) ([afd9cde](https://github.com/Aureliolo/synthorg/commit/afd9cdea0a794ad26a2abbd9ef4cb54ee6f07116))
* bump the all group in /site with 3 updates ([#1178](https://github.com/Aureliolo/synthorg/issues/1178)) ([7cff82a](https://github.com/Aureliolo/synthorg/commit/7cff82a57db67cd492b90b6aad17927e120bf104))
* bump the all group with 2 updates ([#1180](https://github.com/Aureliolo/synthorg/issues/1180)) ([199a1a8](https://github.com/Aureliolo/synthorg/commit/199a1a823addf12a117ffa490d76e21ee8114714))
* bump vitest from 4.1.2 to 4.1.3 in /site in the all group ([#1145](https://github.com/Aureliolo/synthorg/issues/1145)) ([a8c1194](https://github.com/Aureliolo/synthorg/commit/a8c11946b50153301c221f8639df3949ec639176))
* consolidated web deps (11 packages + hono security + test fixes) ([#1150](https://github.com/Aureliolo/synthorg/issues/1150)) ([63a9390](https://github.com/Aureliolo/synthorg/commit/63a9390883c5775c417e509eab1cbcd2779a10ec)), closes [#1147](https://github.com/Aureliolo/synthorg/issues/1147) [#1136](https://github.com/Aureliolo/synthorg/issues/1136) [#1137](https://github.com/Aureliolo/synthorg/issues/1137)
* pin Docker Python base image to 3.14.x ([#1182](https://github.com/Aureliolo/synthorg/issues/1182)) ([8ffdd86](https://github.com/Aureliolo/synthorg/commit/8ffdd868764163c57a83c4bf9bb5127f761c764a))

## [0.6.4](https://github.com/Aureliolo/synthorg/compare/v0.6.3...v0.6.4) (2026-04-07)


### Features

* analytics and metrics runtime pipeline ([#226](https://github.com/Aureliolo/synthorg/issues/226), [#225](https://github.com/Aureliolo/synthorg/issues/225), [#227](https://github.com/Aureliolo/synthorg/issues/227), [#224](https://github.com/Aureliolo/synthorg/issues/224)) ([#1127](https://github.com/Aureliolo/synthorg/issues/1127)) ([ec57641](https://github.com/Aureliolo/synthorg/commit/ec57641bcf207716546a54882c5da5079e27acb1))
* engine intelligence -- quality signals, health monitoring, trajectory scoring, coordination metrics ([#1099](https://github.com/Aureliolo/synthorg/issues/1099)) ([aac2029](https://github.com/Aureliolo/synthorg/commit/aac2029b2e2e6dbd39e661fbd53c47e3d2829309)), closes [#697](https://github.com/Aureliolo/synthorg/issues/697) [#707](https://github.com/Aureliolo/synthorg/issues/707) [#705](https://github.com/Aureliolo/synthorg/issues/705) [#703](https://github.com/Aureliolo/synthorg/issues/703)
* enterprise-grade auth -- HttpOnly cookie sessions, CSRF, lockout, session limits ([#1102](https://github.com/Aureliolo/synthorg/issues/1102)) ([d3022c7](https://github.com/Aureliolo/synthorg/commit/d3022c78132148791a04e948dafa67037a3442e1)), closes [#1068](https://github.com/Aureliolo/synthorg/issues/1068)
* implement core tool categories and granular sub-constraints ([#1101](https://github.com/Aureliolo/synthorg/issues/1101)) ([0611b53](https://github.com/Aureliolo/synthorg/commit/0611b5363ee21f21da36f0ebda16d6eabdd2cb59)), closes [#1034](https://github.com/Aureliolo/synthorg/issues/1034) [#220](https://github.com/Aureliolo/synthorg/issues/220)
* memory evolution -- GraphRAG/consistency research + SelfEditingMemoryStrategy ([#1036](https://github.com/Aureliolo/synthorg/issues/1036), [#208](https://github.com/Aureliolo/synthorg/issues/208)) ([#1129](https://github.com/Aureliolo/synthorg/issues/1129)) ([a9acda3](https://github.com/Aureliolo/synthorg/commit/a9acda3bb565bbecaaa479ca00ac4407d7d6e8a9))
* security hardening -- sandbox, risk override, SSRF self-heal, DAST fix ([#1100](https://github.com/Aureliolo/synthorg/issues/1100)) ([31e7273](https://github.com/Aureliolo/synthorg/commit/31e72736244a634052b231179abd18cbff8dab77)), closes [#1098](https://github.com/Aureliolo/synthorg/issues/1098) [#696](https://github.com/Aureliolo/synthorg/issues/696) [#222](https://github.com/Aureliolo/synthorg/issues/222) [#671](https://github.com/Aureliolo/synthorg/issues/671)


### Bug Fixes

* harden agent identity versioning post-review ([#1128](https://github.com/Aureliolo/synthorg/issues/1128)) ([8eb2859](https://github.com/Aureliolo/synthorg/commit/8eb2859d28513d069e8a8ce622bcc6d23358c72e)), closes [#1076](https://github.com/Aureliolo/synthorg/issues/1076)


### Documentation

* engine architecture research ([#688](https://github.com/Aureliolo/synthorg/issues/688) [#690](https://github.com/Aureliolo/synthorg/issues/690) [#848](https://github.com/Aureliolo/synthorg/issues/848) [#687](https://github.com/Aureliolo/synthorg/issues/687)) ([#1114](https://github.com/Aureliolo/synthorg/issues/1114)) ([59b31f9](https://github.com/Aureliolo/synthorg/commit/59b31f99a50354346f7d9b39d2e7f2ee427834b4))


### Maintenance

* add .claudeignore and split CLAUDE.md for token optimization ([#1112](https://github.com/Aureliolo/synthorg/issues/1112)) ([b0fbd18](https://github.com/Aureliolo/synthorg/commit/b0fbd18ff9f4ff4e81401002e986e730509193b9))
* bump github.com/sigstore/protobuf-specs from 0.5.0 to 0.5.1 in /cli in the all group ([#1106](https://github.com/Aureliolo/synthorg/issues/1106)) ([73089c9](https://github.com/Aureliolo/synthorg/commit/73089c9aa2f3a675bce345cc9ca3baf879eb7dc8))
* bump jsdom from 29.0.1 to 29.0.2 in /site in the all group ([#1107](https://github.com/Aureliolo/synthorg/issues/1107)) ([8e99dce](https://github.com/Aureliolo/synthorg/commit/8e99dce56829e539d367200fc632f5cc387aec2f))
* bump jsdom from 29.0.1 to 29.0.2 in /web in the all group ([#1108](https://github.com/Aureliolo/synthorg/issues/1108)) ([ce8c749](https://github.com/Aureliolo/synthorg/commit/ce8c7492f32e2f77acbd01061f29832e5152deb5))
* bump python from `fb83750` to `6869258` in /docker/backend in the all group ([#1104](https://github.com/Aureliolo/synthorg/issues/1104)) ([4911726](https://github.com/Aureliolo/synthorg/commit/4911726059703e1df06958cb3f07cef131853007))
* bump python from `fb83750` to `6869258` in /docker/web in the all group ([#1103](https://github.com/Aureliolo/synthorg/issues/1103)) ([87bdf09](https://github.com/Aureliolo/synthorg/commit/87bdf091708f2c68bdf99d429ecd0b5b816e789b))
* bump the all group across 1 directory with 4 updates ([#1111](https://github.com/Aureliolo/synthorg/issues/1111)) ([f702464](https://github.com/Aureliolo/synthorg/commit/f7024640e3dc4b62cbc25cca2d450d46c6aa4e21))
* bump the all group in /docker/sandbox with 2 updates ([#1105](https://github.com/Aureliolo/synthorg/issues/1105)) ([05a91ca](https://github.com/Aureliolo/synthorg/commit/05a91ca6a15d0d113f1102983e56e725e9e27758))

## [0.6.3](https://github.com/Aureliolo/synthorg/compare/v0.6.2...v0.6.3) (2026-04-06)


### Features

* backend CRUD + multi-user permissions ([#1081](https://github.com/Aureliolo/synthorg/issues/1081), [#1082](https://github.com/Aureliolo/synthorg/issues/1082)) ([#1094](https://github.com/Aureliolo/synthorg/issues/1094)) ([93e469b](https://github.com/Aureliolo/synthorg/commit/93e469b75441486249fae2c949bd8c8fb30d3f87))
* in-dashboard team editing + budget rebalance on pack apply ([#1093](https://github.com/Aureliolo/synthorg/issues/1093)) ([35977c0](https://github.com/Aureliolo/synthorg/commit/35977c03b0219af1cf293bc7278903321cbc6491)), closes [#1079](https://github.com/Aureliolo/synthorg/issues/1079) [#1080](https://github.com/Aureliolo/synthorg/issues/1080)
* tiered rate limiting, NotificationSink protocol, in-dashboard notifications ([#1092](https://github.com/Aureliolo/synthorg/issues/1092)) ([df2142c](https://github.com/Aureliolo/synthorg/commit/df2142c696855ee7d680fef4e7530994f9047d2c)), closes [#1077](https://github.com/Aureliolo/synthorg/issues/1077) [#1078](https://github.com/Aureliolo/synthorg/issues/1078) [#849](https://github.com/Aureliolo/synthorg/issues/849)
* two-stage safety classifier and cross-provider uncertainty check for approval gates ([#1090](https://github.com/Aureliolo/synthorg/issues/1090)) ([0b2edee](https://github.com/Aureliolo/synthorg/commit/0b2edeecfa5ea414f9521d345f8afb89f020d073)), closes [#847](https://github.com/Aureliolo/synthorg/issues/847) [#701](https://github.com/Aureliolo/synthorg/issues/701)


### Refactoring

* memory pipeline improvements ([#1075](https://github.com/Aureliolo/synthorg/issues/1075), [#997](https://github.com/Aureliolo/synthorg/issues/997)) ([#1091](https://github.com/Aureliolo/synthorg/issues/1091)) ([a048a4c](https://github.com/Aureliolo/synthorg/commit/a048a4c5cb5116d422c2a2f6eca4f34ed46917c2))


### Documentation

* add OpenCode parity setup and hookify rule documentation ([#1095](https://github.com/Aureliolo/synthorg/issues/1095)) ([52e877a](https://github.com/Aureliolo/synthorg/commit/52e877a914571a47f89a89010b6b132d6d32e88a))


### Maintenance

* bump vite from 8.0.3 to 8.0.4 in /web in the all group across 1 directory ([#1088](https://github.com/Aureliolo/synthorg/issues/1088)) ([1e86ca6](https://github.com/Aureliolo/synthorg/commit/1e86ca6e0d45eeb59f512b5a483e04c6597b5fc4))
* tune ZAP DAST scan -- auth, timeouts, rules, report artifacts ([#1097](https://github.com/Aureliolo/synthorg/issues/1097)) ([82bf0e1](https://github.com/Aureliolo/synthorg/commit/82bf0e100731573f4b5da1fec99c1f536c8de728)), closes [#1096](https://github.com/Aureliolo/synthorg/issues/1096)

## [0.6.2](https://github.com/Aureliolo/synthorg/compare/v0.6.1...v0.6.2) (2026-04-06)


### Features

* add issue analyzer script for priority/scope management ([#1084](https://github.com/Aureliolo/synthorg/issues/1084)) ([1ccba27](https://github.com/Aureliolo/synthorg/commit/1ccba275e683e530bb3305e872788d1dffe0e483))
* config fixes and deferred improvements from PR [#1058](https://github.com/Aureliolo/synthorg/issues/1058) review ([#1067](https://github.com/Aureliolo/synthorg/issues/1067)) ([2cac2d3](https://github.com/Aureliolo/synthorg/commit/2cac2d36ac0a80f9b34c7e0a89fe848642aec293)), closes [#1061](https://github.com/Aureliolo/synthorg/issues/1061) [#1060](https://github.com/Aureliolo/synthorg/issues/1060)
* cumulative risk-unit action budgets ([#806](https://github.com/Aureliolo/synthorg/issues/806)) and automated reporting ([#245](https://github.com/Aureliolo/synthorg/issues/245)) ([#1063](https://github.com/Aureliolo/synthorg/issues/1063)) ([4689816](https://github.com/Aureliolo/synthorg/commit/468981622f8e80aee396b964364d8553b73016d5))
* fine-tuning pipeline + CompositeBackend + workflow lifecycle ([#1065](https://github.com/Aureliolo/synthorg/issues/1065)) ([85b05bc](https://github.com/Aureliolo/synthorg/commit/85b05bcb92c3306739ab779fbf615821c271ecad)), closes [#1001](https://github.com/Aureliolo/synthorg/issues/1001) [#850](https://github.com/Aureliolo/synthorg/issues/850) [#1058](https://github.com/Aureliolo/synthorg/issues/1058)
* memory consolidation upgrades (LLM Merge, Search-and-Ask, diversity penalty, distillation capture) ([#1071](https://github.com/Aureliolo/synthorg/issues/1071)) ([174e2be](https://github.com/Aureliolo/synthorg/commit/174e2beb51a945f82fe8c0711782ce8c209eab05)), closes [#704](https://github.com/Aureliolo/synthorg/issues/704)
* migrate web dashboard from Radix UI to Base UI, activate CSP nonce, rebuild org chart page, and fix agent routing ([#1083](https://github.com/Aureliolo/synthorg/issues/1083)) ([ebc6921](https://github.com/Aureliolo/synthorg/commit/ebc6921262b4d918ad9740f7b81342bc8238a7ef))
* v0.7.0 engine foundations -- structured failure diagnosis + auditable decisions ([#1072](https://github.com/Aureliolo/synthorg/issues/1072)) ([d341d37](https://github.com/Aureliolo/synthorg/commit/d341d37616d38a7f2e3a91070ad7692e914c8b12))
* workflow templates and versioning with diff and rollback ([#1069](https://github.com/Aureliolo/synthorg/issues/1069)) ([7af94de](https://github.com/Aureliolo/synthorg/commit/7af94dea57fd8d459b142a563b9aa33cf12392aa)), closes [#1006](https://github.com/Aureliolo/synthorg/issues/1006) [#1008](https://github.com/Aureliolo/synthorg/issues/1008)


### Documentation

* unify REST API docs under /docs/openapi/ and patch sitemap ([#1073](https://github.com/Aureliolo/synthorg/issues/1073)) ([af19382](https://github.com/Aureliolo/synthorg/commit/af193829665398dcd4879bddd35c0ecf4b5d16f0))


### Maintenance

* bump hypothesis from 6.151.10 to 6.151.11 in the all group ([#1086](https://github.com/Aureliolo/synthorg/issues/1086)) ([3176318](https://github.com/Aureliolo/synthorg/commit/31763182a968266a767312b97d45231e2742849f))
* bump nginxinc/nginx-unprivileged from `f99cc61` to `601c823` in /docker/web in the all group ([#1085](https://github.com/Aureliolo/synthorg/issues/1085)) ([5eb99ac](https://github.com/Aureliolo/synthorg/commit/5eb99acc30f80d8a33d44c5b154fd1dc35ba5c89))
* bump the all group in /web with 3 updates ([#1087](https://github.com/Aureliolo/synthorg/issues/1087)) ([8deae44](https://github.com/Aureliolo/synthorg/commit/8deae44ec96b3c68eb95f3556c5ed9bb48214095))

## [0.6.1](https://github.com/Aureliolo/synthorg/compare/v0.6.0...v0.6.1) (2026-04-04)


### Features

* capability-aware prompt profiles for model tier adaptation ([#1047](https://github.com/Aureliolo/synthorg/issues/1047)) ([67650c5](https://github.com/Aureliolo/synthorg/commit/67650c542d31fe8da38bc6d1a3a87ef24790c36f)), closes [#805](https://github.com/Aureliolo/synthorg/issues/805)
* implement procedural memory auto-generation from agent failures ([#1048](https://github.com/Aureliolo/synthorg/issues/1048)) ([55f5206](https://github.com/Aureliolo/synthorg/commit/55f52068c7ae1386a760989bfacd02baede2067b)), closes [#420](https://github.com/Aureliolo/synthorg/issues/420)
* implement quality scoring Layers 2+3 -- LLM judge and human override ([#1057](https://github.com/Aureliolo/synthorg/issues/1057)) ([4a8adfe](https://github.com/Aureliolo/synthorg/commit/4a8adfec13c3fbe77cbb6cdd04c5aaeb04b94aa0)), closes [#230](https://github.com/Aureliolo/synthorg/issues/230)
* token-based personality trimming via PromptProfile.max_personality_tokens ([#1059](https://github.com/Aureliolo/synthorg/issues/1059)) ([75afd52](https://github.com/Aureliolo/synthorg/commit/75afd520351bc217b4bfa022ff8a9f4663e839f9)), closes [#1045](https://github.com/Aureliolo/synthorg/issues/1045)
* workflow execution lifecycle + editor improvements ([#1058](https://github.com/Aureliolo/synthorg/issues/1058)) ([7b54262](https://github.com/Aureliolo/synthorg/commit/7b542626324eb7bdfecf9afb6f837e42b9b54d2a)), closes [#1029](https://github.com/Aureliolo/synthorg/issues/1029) [#1042](https://github.com/Aureliolo/synthorg/issues/1042)


### Refactoring

* **web:** address complexity and logging issues in dashboard ([#1056](https://github.com/Aureliolo/synthorg/issues/1056)) ([ada997b](https://github.com/Aureliolo/synthorg/commit/ada997bfc1064ac797faebabf8d1d822abe75aa6)), closes [#1055](https://github.com/Aureliolo/synthorg/issues/1055)


### Documentation

* comprehensive documentation refresh ([#1050](https://github.com/Aureliolo/synthorg/issues/1050)) ([c7a4259](https://github.com/Aureliolo/synthorg/commit/c7a4259ed2a83aa307fde390c09e6b76e513cb7b))


### Tests

* fix Hypothesis fuzzing infra and speed up slow unit tests ([#1044](https://github.com/Aureliolo/synthorg/issues/1044)) ([1111602](https://github.com/Aureliolo/synthorg/commit/1111602d5e03b1856434c10c801b005e3ee2d7bd))


### Maintenance

* add text=auto catch-all to .gitattributes ([#1051](https://github.com/Aureliolo/synthorg/issues/1051)) ([fc65d72](https://github.com/Aureliolo/synthorg/commit/fc65d72ef1ba5937cfbe4feba22571131bb08857))
* bump defu from 6.1.4 to 6.1.6 in /site ([#1062](https://github.com/Aureliolo/synthorg/issues/1062)) ([f0cc439](https://github.com/Aureliolo/synthorg/commit/f0cc4391e64ac20bda04a673af0a8d55f95e5c31))

## [0.6.0](https://github.com/Aureliolo/synthorg/compare/v0.5.9...v0.6.0) (2026-04-03)


### Features

* dashboard UI for ceremony policy settings ([#1038](https://github.com/Aureliolo/synthorg/issues/1038)) ([865554c](https://github.com/Aureliolo/synthorg/commit/865554cb8fb1ac4384be0994d45ba566357cc926)), closes [#979](https://github.com/Aureliolo/synthorg/issues/979)
* implement tool-based memory retrieval injection strategy ([#1039](https://github.com/Aureliolo/synthorg/issues/1039)) ([329270e](https://github.com/Aureliolo/synthorg/commit/329270e7c9d07e25af5816073e9a243e88fbaffd)), closes [#207](https://github.com/Aureliolo/synthorg/issues/207)
* local model management for Ollama and LM Studio ([#1037](https://github.com/Aureliolo/synthorg/issues/1037)) ([e1b14d3](https://github.com/Aureliolo/synthorg/commit/e1b14d39aee4b1bead327bf395342c89d7fb4e43)), closes [#1030](https://github.com/Aureliolo/synthorg/issues/1030)
* workflow execution -- instantiate tasks from WorkflowDefinition ([#1040](https://github.com/Aureliolo/synthorg/issues/1040)) ([e9235e3](https://github.com/Aureliolo/synthorg/commit/e9235e3da4d23b08062c7e4bdf860eb5780ac963)), closes [#1004](https://github.com/Aureliolo/synthorg/issues/1004)


### Maintenance

* shared Hypothesis failure DB + deterministic CI profile ([#1041](https://github.com/Aureliolo/synthorg/issues/1041)) ([901ae92](https://github.com/Aureliolo/synthorg/commit/901ae92ced6704c1d89c28948c619cee357e72db))

## [0.5.9](https://github.com/Aureliolo/synthorg/compare/v0.5.8...v0.5.9) (2026-04-03)


### Features

* ceremony template defaults + strategy migration UX ([#1031](https://github.com/Aureliolo/synthorg/issues/1031)) ([da4a8e1](https://github.com/Aureliolo/synthorg/commit/da4a8e1bd44fb2ee634b5d5c0c5cf74c5c7d3450)), closes [#976](https://github.com/Aureliolo/synthorg/issues/976) [#978](https://github.com/Aureliolo/synthorg/issues/978)
* hybrid search (dense + BM25 sparse) for memory retrieval pipeline ([#1016](https://github.com/Aureliolo/synthorg/issues/1016)) ([fccac4a](https://github.com/Aureliolo/synthorg/commit/fccac4af9b284a4d0fe1ef7ea2f2ca523476b612)), closes [#694](https://github.com/Aureliolo/synthorg/issues/694)
* implement network hosting and multi-user access ([#1032](https://github.com/Aureliolo/synthorg/issues/1032)) ([398c378](https://github.com/Aureliolo/synthorg/commit/398c378446e47dc5a2af1d8742e1810d66664420)), closes [#244](https://github.com/Aureliolo/synthorg/issues/244)
* implement visual workflow editor ([#247](https://github.com/Aureliolo/synthorg/issues/247)) ([#1018](https://github.com/Aureliolo/synthorg/issues/1018)) ([ef5d3c1](https://github.com/Aureliolo/synthorg/commit/ef5d3c1e7de4d8e303c5d55ace5408e9b865c726))

## [0.5.8](https://github.com/Aureliolo/synthorg/compare/v0.5.7...v0.5.8) (2026-04-03)


### Features

* auto-select embedding model + fine-tuning pipeline wiring ([#999](https://github.com/Aureliolo/synthorg/issues/999)) ([a4cbc4e](https://github.com/Aureliolo/synthorg/commit/a4cbc4e99949ae2c8cc7cf02706b7fb2d6242ca0)), closes [#965](https://github.com/Aureliolo/synthorg/issues/965) [#966](https://github.com/Aureliolo/synthorg/issues/966)
* ceremony scheduling batch 3 -- milestone strategy, template defaults, department overrides ([#1019](https://github.com/Aureliolo/synthorg/issues/1019)) ([321d245](https://github.com/Aureliolo/synthorg/commit/321d24562eeeac755c240fff130c945d05d7d745))
* five-pillar evaluation framework for HR performance tracking ([#1017](https://github.com/Aureliolo/synthorg/issues/1017)) ([5e66cbd](https://github.com/Aureliolo/synthorg/commit/5e66cbd1aa928bb4a61e5a11fc143acdc3355aba)), closes [#699](https://github.com/Aureliolo/synthorg/issues/699)
* populate comparison page with 53 competitor entries ([#1000](https://github.com/Aureliolo/synthorg/issues/1000)) ([5cb232d](https://github.com/Aureliolo/synthorg/commit/5cb232d1dc8cd7703eb173b243c34b5e0e6fc287)), closes [#993](https://github.com/Aureliolo/synthorg/issues/993)
* throughput-adaptive and external-trigger ceremony scheduling strategies ([#1003](https://github.com/Aureliolo/synthorg/issues/1003)) ([bb5c9a4](https://github.com/Aureliolo/synthorg/commit/bb5c9a4f71235d121cbfd730e6467c8011f1c081)), closes [#973](https://github.com/Aureliolo/synthorg/issues/973) [#974](https://github.com/Aureliolo/synthorg/issues/974)


### Bug Fixes

* eliminate backup service I/O from API test lifecycle ([#1015](https://github.com/Aureliolo/synthorg/issues/1015)) ([08d9183](https://github.com/Aureliolo/synthorg/commit/08d9183dc9d028516dc532b7b9a98f27678b0401))
* update run_affected_tests.py to use -n 8 ([#1014](https://github.com/Aureliolo/synthorg/issues/1014)) ([3ee9fa7](https://github.com/Aureliolo/synthorg/commit/3ee9fa7d8af52f96ab9ba14277b943ec09130ee1))


### Performance

* reduce pytest parallelism from -n auto to -n 8 ([#1013](https://github.com/Aureliolo/synthorg/issues/1013)) ([43e0707](https://github.com/Aureliolo/synthorg/commit/43e07070635697511f5a61bc6e2b0aea6d219ce8))


### CI/CD

* bump docker/login-action from 4.0.0 to 4.1.0 in the all group ([#1027](https://github.com/Aureliolo/synthorg/issues/1027)) ([e7e28ec](https://github.com/Aureliolo/synthorg/commit/e7e28ecca8814aaa6f060aeb25ed281f433d3ab0))
* bump wrangler from 4.79.0 to 4.80.0 in /.github in the all group ([#1023](https://github.com/Aureliolo/synthorg/issues/1023)) ([1322a0d](https://github.com/Aureliolo/synthorg/commit/1322a0dc5623e094f6e1869d02628970b12bd149))


### Maintenance

* bump github.com/mattn/go-runewidth from 0.0.21 to 0.0.22 in /cli in the all group ([#1024](https://github.com/Aureliolo/synthorg/issues/1024)) ([b311694](https://github.com/Aureliolo/synthorg/commit/b3116944d3b9e82c5c691fe7836ed72b01b9426e))
* bump https://github.com/astral-sh/ruff-pre-commit from v0.15.8 to 0.15.9 in the all group ([#1022](https://github.com/Aureliolo/synthorg/issues/1022)) ([1650087](https://github.com/Aureliolo/synthorg/commit/16500878a35913454f4062ea51596aa038e30869))
* bump node from `71be405` to `387eebd` in /docker/sandbox in the all group ([#1021](https://github.com/Aureliolo/synthorg/issues/1021)) ([40bd2f6](https://github.com/Aureliolo/synthorg/commit/40bd2f683a63b8d8301c728aff1bd3547cfaf978))
* bump node from `cf38e1f` to `ad82eca` in /docker/web in the all group ([#1020](https://github.com/Aureliolo/synthorg/issues/1020)) ([f05ab9f](https://github.com/Aureliolo/synthorg/commit/f05ab9ffcfa912c946134e5a02e78641be5ae6d0))
* bump the all group in /web with 3 updates ([#1025](https://github.com/Aureliolo/synthorg/issues/1025)) ([21d40d3](https://github.com/Aureliolo/synthorg/commit/21d40d3a378040d9eb32bd0db628c2d9b642f30f))
* bump the all group with 2 updates ([#1026](https://github.com/Aureliolo/synthorg/issues/1026)) ([36778de](https://github.com/Aureliolo/synthorg/commit/36778de3e5c601dec2fcbb1b7c8bb1765d41a0fc))
* enable additional eslint-react rules and fix violations ([#1028](https://github.com/Aureliolo/synthorg/issues/1028)) ([80423be](https://github.com/Aureliolo/synthorg/commit/80423beccfbf3b6c948dcae4a6d72268937dfd25))

## [0.5.7](https://github.com/Aureliolo/synthorg/compare/v0.5.6...v0.5.7) (2026-04-02)


### Features

* comparison page -- SynthOrg vs agent orchestration frameworks ([#994](https://github.com/Aureliolo/synthorg/issues/994)) ([6f937ef](https://github.com/Aureliolo/synthorg/commit/6f937efb1ce43cb9dca2979cc13756dba017de1c)), closes [#981](https://github.com/Aureliolo/synthorg/issues/981)
* event-driven and budget-driven ceremony scheduling strategies ([#995](https://github.com/Aureliolo/synthorg/issues/995)) ([f88e7b0](https://github.com/Aureliolo/synthorg/commit/f88e7b0a76e5ff49db04dd86304644e431c8ba32)), closes [#971](https://github.com/Aureliolo/synthorg/issues/971) [#972](https://github.com/Aureliolo/synthorg/issues/972)
* template packs for post-setup additive team expansion ([#996](https://github.com/Aureliolo/synthorg/issues/996)) ([b45e14a](https://github.com/Aureliolo/synthorg/commit/b45e14a564bbd2c27a1bdb9b191b6f4024629c8a)), closes [#727](https://github.com/Aureliolo/synthorg/issues/727)


### Performance

* preload JetBrains Mono font, remove unused api.github.com preconnect ([#998](https://github.com/Aureliolo/synthorg/issues/998)) ([2a189c2](https://github.com/Aureliolo/synthorg/commit/2a189c2171b996d632dd5ce7ee490043f4ca03fd))
* run only affected modules in pre-push hooks ([#992](https://github.com/Aureliolo/synthorg/issues/992)) ([7956e23](https://github.com/Aureliolo/synthorg/commit/7956e2335a6ce507f7812e971f52275daf1da621))


### Maintenance

* bump astro from 6.1.2 to 6.1.3 in /site in the all group ([#988](https://github.com/Aureliolo/synthorg/issues/988)) ([17b58db](https://github.com/Aureliolo/synthorg/commit/17b58db23dde3c84d09d4cb98be7c550007c74e4))
* bump the all group across 1 directory with 2 updates ([#989](https://github.com/Aureliolo/synthorg/issues/989)) ([1ff462a](https://github.com/Aureliolo/synthorg/commit/1ff462a841ad96870953812242ce73688a667c40))

## [0.5.6](https://github.com/Aureliolo/synthorg/compare/v0.5.5...v0.5.6) (2026-04-02)


### Features

* calendar + hybrid ceremony scheduling strategies ([#985](https://github.com/Aureliolo/synthorg/issues/985)) ([59a9b84](https://github.com/Aureliolo/synthorg/commit/59a9b84486702b6b3dcd2561f200922ae4c6a5f2)), closes [#969](https://github.com/Aureliolo/synthorg/issues/969) [#970](https://github.com/Aureliolo/synthorg/issues/970)
* landing page interactive components ([#984](https://github.com/Aureliolo/synthorg/issues/984)) ([49868cb](https://github.com/Aureliolo/synthorg/commit/49868cb4d25d58e64a0d2786ad1f678404b00ffc))
* log aggregation and shipping (syslog, HTTP, compression) ([#964](https://github.com/Aureliolo/synthorg/issues/964)) ([84be9f8](https://github.com/Aureliolo/synthorg/commit/84be9f8dc6142a7feb29198c4411975a07a95890))
* restructure builtin templates into inheritance tree ([#982](https://github.com/Aureliolo/synthorg/issues/982)) ([3794c12](https://github.com/Aureliolo/synthorg/commit/3794c12facfc29e9c23d808dfb69dca4f31c93f8))
* sprint ceremony runtime scheduler with pluggable strategies ([#983](https://github.com/Aureliolo/synthorg/issues/983)) ([43564a9](https://github.com/Aureliolo/synthorg/commit/43564a99a69ac5c04608a9a8813403f0f7b7a355))


### Maintenance

* add no-bash-file-writes rule to CLAUDE.md ([#968](https://github.com/Aureliolo/synthorg/issues/968)) ([a854dcc](https://github.com/Aureliolo/synthorg/commit/a854dccac97432288c6af8b1575ab2d550551735))
* bump web dependencies (lodash, eslint-react v4, storybook, playwright, esbuild, codemirror) ([#987](https://github.com/Aureliolo/synthorg/issues/987)) ([c344dfb](https://github.com/Aureliolo/synthorg/commit/c344dfb4bf407d15e27188d00172ac50d3ab0763))

## [0.5.5](https://github.com/Aureliolo/synthorg/compare/v0.5.4...v0.5.5) (2026-04-01)


### Features

* add workflow configs to builtin templates ([#963](https://github.com/Aureliolo/synthorg/issues/963)) ([b7fe6e3](https://github.com/Aureliolo/synthorg/commit/b7fe6e3101626b55bd235f09f5748e5e29d758b4))
* implement Kanban board and Agile sprints workflow types ([#960](https://github.com/Aureliolo/synthorg/issues/960)) ([f511e1d](https://github.com/Aureliolo/synthorg/commit/f511e1d6e0323babc9480df529d986a66fae54eb))
* personality preset support in template YAML schema ([#959](https://github.com/Aureliolo/synthorg/issues/959)) ([97ca81e](https://github.com/Aureliolo/synthorg/commit/97ca81e232517b60d64a56c47b6aefdb12baa1f3))


### Documentation

* LMEB embedding evaluation + CSP accepted risk ([#695](https://github.com/Aureliolo/synthorg/issues/695), [#925](https://github.com/Aureliolo/synthorg/issues/925)) ([#962](https://github.com/Aureliolo/synthorg/issues/962)) ([43dfab3](https://github.com/Aureliolo/synthorg/commit/43dfab3d8683950ddca845e6f2e307a35486dd2f))


### CI/CD

* bump wrangler from 4.78.0 to 4.79.0 in /.github in the all group across 1 directory ([#955](https://github.com/Aureliolo/synthorg/issues/955)) ([18b4cb1](https://github.com/Aureliolo/synthorg/commit/18b4cb13707ffa2976e485db08fccf7a60982ce2))


### Maintenance

* bump mypy from 1.19.1 to 1.20.0 in the all group across 1 directory ([#956](https://github.com/Aureliolo/synthorg/issues/956)) ([29cc419](https://github.com/Aureliolo/synthorg/commit/29cc41981a7effc722453db79fb96845bcde91e6))

## [0.5.4](https://github.com/Aureliolo/synthorg/compare/v0.5.3...v0.5.4) (2026-04-01)


### Features

* artifact and project management UI in web dashboard ([#954](https://github.com/Aureliolo/synthorg/issues/954)) ([00a0430](https://github.com/Aureliolo/synthorg/commit/00a0430336a00d99a60bb2def1dc0e02f8895cc0))
* embed MkDocs build output in React web dashboard at /docs ([#948](https://github.com/Aureliolo/synthorg/issues/948)) ([f229fc2](https://github.com/Aureliolo/synthorg/commit/f229fc2376b1ada584ea01367f57a72b1201a582))
* personality preset discovery API and user-defined preset CRUD ([#952](https://github.com/Aureliolo/synthorg/issues/952)) ([497848a](https://github.com/Aureliolo/synthorg/commit/497848a2da8efd5fa1c3e002a3aaa314df731001))
* support multi-provider model resolution with budget-based selection ([#953](https://github.com/Aureliolo/synthorg/issues/953)) ([146b782](https://github.com/Aureliolo/synthorg/commit/146b78296176cb72332f0f2bfcbd61b03f32fcc2))
* support per-agent memory retention overrides ([#209](https://github.com/Aureliolo/synthorg/issues/209)) ([#951](https://github.com/Aureliolo/synthorg/issues/951)) ([020c610](https://github.com/Aureliolo/synthorg/commit/020c610fe19ffaaa6144b5e28f70db104681bfc7))


### Documentation

* write user guides and tutorials ([#949](https://github.com/Aureliolo/synthorg/issues/949)) ([1367225](https://github.com/Aureliolo/synthorg/commit/136722517230f5240a6d2baf1280db835fb6a8fc))

## [0.5.3](https://github.com/Aureliolo/synthorg/compare/v0.5.2...v0.5.3) (2026-03-31)


### Features

* implement artifact and project persistence ([#947](https://github.com/Aureliolo/synthorg/issues/947)) ([6dea87a](https://github.com/Aureliolo/synthorg/commit/6dea87a8325be35d68b225f6c6a9dc587a4e7a11))


### Maintenance

* add allow_inf_nan=False to all remaining ConfigDict declarations ([#943](https://github.com/Aureliolo/synthorg/issues/943)) ([cd7bbca](https://github.com/Aureliolo/synthorg/commit/cd7bbca14039d66b47611ed583f7ab25649f1db6))
* audit full web dashboard for hardcoded design token violations ([#944](https://github.com/Aureliolo/synthorg/issues/944)) ([a1322cd](https://github.com/Aureliolo/synthorg/commit/a1322cd6f64a4e3ec1a30da586de2e26c87073ff))

## [0.5.2](https://github.com/Aureliolo/synthorg/compare/v0.5.1...v0.5.2) (2026-03-31)


### Features

* harden activity feed API ([#838](https://github.com/Aureliolo/synthorg/issues/838), [#839](https://github.com/Aureliolo/synthorg/issues/839), [#840](https://github.com/Aureliolo/synthorg/issues/840)) ([#937](https://github.com/Aureliolo/synthorg/issues/937)) ([c0234ad](https://github.com/Aureliolo/synthorg/commit/c0234ad10e1963e2939ca80656f05e38fc2dcbc1))
* provider usage metrics, model capabilities, and active health probing ([#935](https://github.com/Aureliolo/synthorg/issues/935)) ([1434c9c](https://github.com/Aureliolo/synthorg/commit/1434c9ccea52b17ff7c7bd1f197d0f7c02765b80))
* runtime sink configuration via SettingsService ([#934](https://github.com/Aureliolo/synthorg/issues/934)) ([16c3f23](https://github.com/Aureliolo/synthorg/commit/16c3f230e3bb0d37e46a5ec7932c5eab11990499))
* Settings page comprehensive redesign ([#936](https://github.com/Aureliolo/synthorg/issues/936)) ([#939](https://github.com/Aureliolo/synthorg/issues/939)) ([6d9ac8b](https://github.com/Aureliolo/synthorg/commit/6d9ac8bc234c2ee64a7941fcc5caf33c2538b823))


### Maintenance

* bump astro from 6.1.1 to 6.1.2 in /site in the all group ([#940](https://github.com/Aureliolo/synthorg/issues/940)) ([ffa24f0](https://github.com/Aureliolo/synthorg/commit/ffa24f0807a478f0eefbfca5233a5dd8253fdd4a))
* bump pygments from 2.19.2 to 2.20.0 ([#931](https://github.com/Aureliolo/synthorg/issues/931)) ([9993088](https://github.com/Aureliolo/synthorg/commit/9993088323fcb830f0197f498ebadbecff043655))
* bump the all group with 2 updates ([#942](https://github.com/Aureliolo/synthorg/issues/942)) ([aea37f8](https://github.com/Aureliolo/synthorg/commit/aea37f8914a288856bc187f09518b42588f7b5d0))
* bump typescript-eslint from 8.57.2 to 8.58.0 in /web in the all group ([#941](https://github.com/Aureliolo/synthorg/issues/941)) ([24f024c](https://github.com/Aureliolo/synthorg/commit/24f024cf744cbfb71e7051e29ee66bc953d6d807))
* split CLAUDE.md into subdirectory files for cli/ and web/ ([#932](https://github.com/Aureliolo/synthorg/issues/932)) ([f5cfe07](https://github.com/Aureliolo/synthorg/commit/f5cfe0753a155ff7f4d397e955e37fe5de79f5d2))

## [0.5.1](https://github.com/Aureliolo/synthorg/compare/v0.5.0...v0.5.1) (2026-03-30)


### Features

* add linear variant to ProgressGauge component ([#927](https://github.com/Aureliolo/synthorg/issues/927)) ([89bf8d0](https://github.com/Aureliolo/synthorg/commit/89bf8d0d40b543ff7a6f28912c22573df1660b0d))
* frontend security hardening -- ESLint XSS ban + MotionConfig CSP nonce ([#926](https://github.com/Aureliolo/synthorg/issues/926)) ([6592ed0](https://github.com/Aureliolo/synthorg/commit/6592ed038c2aa5d9b5e9068ec0779cc96626f988))
* set up MSW for Storybook API mocking ([#930](https://github.com/Aureliolo/synthorg/issues/930)) ([214078c](https://github.com/Aureliolo/synthorg/commit/214078c3bfb8cd12aebbc04002be16510cd74c31))


### Refactoring

* **web:** replace Sidebar tablet overlay with shared Drawer component ([#928](https://github.com/Aureliolo/synthorg/issues/928)) ([ad5451d](https://github.com/Aureliolo/synthorg/commit/ad5451dc2223d7866366b7c21c56eb3ccdacd417))

## [0.5.0](https://github.com/Aureliolo/synthorg/compare/v0.4.9...v0.5.0) (2026-03-30)


### Features

* add analytics trends and budget forecast API endpoints ([#798](https://github.com/Aureliolo/synthorg/issues/798)) ([16b61f5](https://github.com/Aureliolo/synthorg/commit/16b61f57e66766c56dce941887b1af32369366d5))
* add department policies to default templates ([#852](https://github.com/Aureliolo/synthorg/issues/852)) ([7a41548](https://github.com/Aureliolo/synthorg/commit/7a41548efe774d09f5455c477919aa9e6eff44ab))
* add remaining activity event types (task_started, tool_used, delegation, cost_incurred) ([#832](https://github.com/Aureliolo/synthorg/issues/832)) ([4252fac](https://github.com/Aureliolo/synthorg/commit/4252fac35990a03a9386f4d6b326d07850de90a1))
* agent performance, activity, and history API endpoints ([#811](https://github.com/Aureliolo/synthorg/issues/811)) ([9b75c1d](https://github.com/Aureliolo/synthorg/commit/9b75c1d545974e9b6d6627964a52e6bba2a05e9d))
* Agent Profiles and Detail pages (biography, career, performance) ([#874](https://github.com/Aureliolo/synthorg/issues/874)) ([62d7880](https://github.com/Aureliolo/synthorg/commit/62d7880331aaf3440d0af49b7e983ea840256754))
* app shell, Storybook, and CI/CD pipeline ([#819](https://github.com/Aureliolo/synthorg/issues/819)) ([d4dde90](https://github.com/Aureliolo/synthorg/commit/d4dde904e2c3307f09b16ec2d505278cdc19cc29))
* Approvals page with risk grouping, urgency indicators, batch actions ([#889](https://github.com/Aureliolo/synthorg/issues/889)) ([4e9673d](https://github.com/Aureliolo/synthorg/commit/4e9673da9e03e48a76c4fcbbb8f9b90cc6ecfd65))
* Budget Panel page (P&L dashboard, breakdown charts, forecast) ([#890](https://github.com/Aureliolo/synthorg/issues/890)) ([b63b0f1](https://github.com/Aureliolo/synthorg/commit/b63b0f16d5d36e485ea05f2ab7f3e09984954f18))
* build infrastructure layer (API client, auth, WebSocket) ([#815](https://github.com/Aureliolo/synthorg/issues/815)) ([9f01d3e](https://github.com/Aureliolo/synthorg/commit/9f01d3e830bef32077ec06122f96680af0557afd))
* CLI global options infrastructure, UI modes, exit codes, env vars ([#891](https://github.com/Aureliolo/synthorg/issues/891)) ([fef4fc5](https://github.com/Aureliolo/synthorg/commit/fef4fc540d0db4c6b02a90f3d2d732327f476b71))
* CodeMirror editor and theme preferences toggle ([#905](https://github.com/Aureliolo/synthorg/issues/905), [#807](https://github.com/Aureliolo/synthorg/issues/807)) ([#909](https://github.com/Aureliolo/synthorg/issues/909)) ([41fbedc](https://github.com/Aureliolo/synthorg/commit/41fbedc9771ccc9f9a341c463bbc8b76aecba71f))
* Company page (department/agent management) ([#888](https://github.com/Aureliolo/synthorg/issues/888)) ([cfb88b0](https://github.com/Aureliolo/synthorg/commit/cfb88b01c5804986ae634c024fa175d7c5ee7f30))
* comprehensive hint coverage across all CLI commands ([#900](https://github.com/Aureliolo/synthorg/issues/900)) ([937974e](https://github.com/Aureliolo/synthorg/commit/937974e77dfda2df466d8d7f769901120cc4123a))
* config system extensions, per-command flags for init/start/stop/status/logs ([#895](https://github.com/Aureliolo/synthorg/issues/895)) ([32f83fe](https://github.com/Aureliolo/synthorg/commit/32f83fe2e9516ef22fff8fe6b4b2df34b907281f))
* configurable currency system replacing hardcoded USD ([#854](https://github.com/Aureliolo/synthorg/issues/854)) ([b372551](https://github.com/Aureliolo/synthorg/commit/b37255137673d9782f0eb83dc7c80d80ac3c6acb))
* Dashboard page (metric cards, activity feed, budget burn) ([#861](https://github.com/Aureliolo/synthorg/issues/861)) ([7d519d5](https://github.com/Aureliolo/synthorg/commit/7d519d56601f16e45bb4130143f783a778250e6c))
* department health, provider status, and activity feed endpoints ([#818](https://github.com/Aureliolo/synthorg/issues/818)) ([6d5f196](https://github.com/Aureliolo/synthorg/commit/6d5f196b4b1058824ff0a5e1ec3caaa807234c56))
* design tokens and core UI components ([#833](https://github.com/Aureliolo/synthorg/issues/833)) ([ed887f2](https://github.com/Aureliolo/synthorg/commit/ed887f251187dfbcd1a642ab53fa40f210ec59b9))
* extend approval, meeting, and budget API responses ([#834](https://github.com/Aureliolo/synthorg/issues/834)) ([31472bf](https://github.com/Aureliolo/synthorg/commit/31472bfdbc34be43f29e157894eec3824d3d776c))
* frontend polish -- real-time UX, accessibility, responsive, performance ([#790](https://github.com/Aureliolo/synthorg/issues/790), [#792](https://github.com/Aureliolo/synthorg/issues/792), [#791](https://github.com/Aureliolo/synthorg/issues/791), [#793](https://github.com/Aureliolo/synthorg/issues/793)) ([#917](https://github.com/Aureliolo/synthorg/issues/917)) ([f04a537](https://github.com/Aureliolo/synthorg/commit/f04a53751cf6e0bdf7a7e93153ffc362d69b6f26))
* implement human roles and access control levels ([#856](https://github.com/Aureliolo/synthorg/issues/856)) ([d6d8a06](https://github.com/Aureliolo/synthorg/commit/d6d8a06917db687d6400f8241b32ef6d28b64272))
* implement semantic conflict detection in workspace merge ([#860](https://github.com/Aureliolo/synthorg/issues/860)) ([d97283b](https://github.com/Aureliolo/synthorg/commit/d97283b8efb868e4058160a7b3c58c8a0db821bc))
* interaction components and animation patterns ([#853](https://github.com/Aureliolo/synthorg/issues/853)) ([82d4b01](https://github.com/Aureliolo/synthorg/commit/82d4b0132c47ba8eda06364c82be7b07b30984b6))
* Login page + first-run bootstrap + Company page ([#789](https://github.com/Aureliolo/synthorg/issues/789), [#888](https://github.com/Aureliolo/synthorg/issues/888)) ([#896](https://github.com/Aureliolo/synthorg/issues/896)) ([8758e8d](https://github.com/Aureliolo/synthorg/commit/8758e8d0052f13b483c9c61a39edd6b8612e9b6d))
* Meetings page with timeline viz, token bars, contribution formatting ([#788](https://github.com/Aureliolo/synthorg/issues/788)) ([#904](https://github.com/Aureliolo/synthorg/issues/904)) ([b207f46](https://github.com/Aureliolo/synthorg/commit/b207f467f8ded691fa164a4c93d301ff66e2fa63))
* Messages page with threading, channel badges, sender indicators ([#787](https://github.com/Aureliolo/synthorg/issues/787)) ([#903](https://github.com/Aureliolo/synthorg/issues/903)) ([28293ad](https://github.com/Aureliolo/synthorg/commit/28293ad5b3b30a2c7e35aeac7622f8efb44e7746))
* Org Chart force-directed view and drag-drop reassignment ([#872](https://github.com/Aureliolo/synthorg/issues/872), [#873](https://github.com/Aureliolo/synthorg/issues/873)) ([#912](https://github.com/Aureliolo/synthorg/issues/912)) ([a68a938](https://github.com/Aureliolo/synthorg/commit/a68a93896d9e6ff8edab2179b6148895b2bed10d))
* Org Chart page (living nodes, status, CRUD, department health) ([#870](https://github.com/Aureliolo/synthorg/issues/870)) ([0acbdae](https://github.com/Aureliolo/synthorg/commit/0acbdae65cf7250535990e093041aaaecc0097e7))
* per-command flags for remaining commands, auto-behavior wiring, help/discoverability ([#897](https://github.com/Aureliolo/synthorg/issues/897)) ([3f7afa2](https://github.com/Aureliolo/synthorg/commit/3f7afa2bb97ccf392cfcdd16663a945ecb15403a))
* Providers page with backend rework -- health, CRUD, subscription auth ([#893](https://github.com/Aureliolo/synthorg/issues/893)) ([9f8dd98](https://github.com/Aureliolo/synthorg/commit/9f8dd98883f1fe29d5da18434f7a226fa4b4443d))
* scaffold React + Vite + TypeScript + Tailwind project ([#799](https://github.com/Aureliolo/synthorg/issues/799)) ([bd151aa](https://github.com/Aureliolo/synthorg/commit/bd151aa4b1f5fff639c6052343760a16c54a6f2a))
* Settings page with search, dependency indicators, grouped rendering ([#784](https://github.com/Aureliolo/synthorg/issues/784)) ([#902](https://github.com/Aureliolo/synthorg/issues/902)) ([a7b9870](https://github.com/Aureliolo/synthorg/commit/a7b9870bac65c049c997fcecd96b42ffaf011fcb))
* Setup Wizard rebuild with template comparison, cost estimator, theme customization ([#879](https://github.com/Aureliolo/synthorg/issues/879)) ([ae8b50b](https://github.com/Aureliolo/synthorg/commit/ae8b50b1190f4f5bd878ca2fa2b7e30bff805eaa))
* setup wizard UX -- template filters, card metadata, provider form reuse ([#910](https://github.com/Aureliolo/synthorg/issues/910)) ([7f04676](https://github.com/Aureliolo/synthorg/commit/7f04676b6c48e2e39f6001b84904706a7c07fd50))
* setup wizard UX overhaul -- mode choice, step reorder, provider fixes ([#907](https://github.com/Aureliolo/synthorg/issues/907)) ([ee964c4](https://github.com/Aureliolo/synthorg/commit/ee964c409665859e32ca24498f2780e8fb517410))
* structured ModelRequirement in template agent configs ([#795](https://github.com/Aureliolo/synthorg/issues/795)) ([7433548](https://github.com/Aureliolo/synthorg/commit/74335484221414986c60a65276779194332b5096))
* Task Board page (rich Kanban, filtering, dependency viz) ([#871](https://github.com/Aureliolo/synthorg/issues/871)) ([04a19b0](https://github.com/Aureliolo/synthorg/commit/04a19b09e16f3ef3954e0e059f92ea3f116dc6e2))


### Bug Fixes

* align frontend types with backend and debounce WS refetches ([#916](https://github.com/Aureliolo/synthorg/issues/916)) ([134c11b](https://github.com/Aureliolo/synthorg/commit/134c11be6c2c6b47cb04d75a05580ae8f7a1157e))
* auto-cleanup targets newly pulled images instead of old ones ([#884](https://github.com/Aureliolo/synthorg/issues/884)) ([50e6591](https://github.com/Aureliolo/synthorg/commit/50e6591b9497c5cc19fdd751d4148cc351a72f7f))
* correct wipe backup-skip flow and harden error handling ([#808](https://github.com/Aureliolo/synthorg/issues/808)) ([c05860f](https://github.com/Aureliolo/synthorg/commit/c05860fc78c657c7f494c0b503651efddd857885))
* improve provider setup in wizard, subscription auth, dashboard bugs ([#914](https://github.com/Aureliolo/synthorg/issues/914)) ([87bf8e6](https://github.com/Aureliolo/synthorg/commit/87bf8e637bf3c8e77bec89f448177c647b89ad5c))
* improve update channel detection and add config get command ([#814](https://github.com/Aureliolo/synthorg/issues/814)) ([6b137f0](https://github.com/Aureliolo/synthorg/commit/6b137f015d707b6904833e25d04f16c2a8af22fc))
* resolve all ESLint warnings, add zero-warnings enforcement ([#899](https://github.com/Aureliolo/synthorg/issues/899)) ([079b46a](https://github.com/Aureliolo/synthorg/commit/079b46af2fcab963e77da7482898905d8214daa0))
* subscription auth uses api_key, base URL optional for cloud providers ([#915](https://github.com/Aureliolo/synthorg/issues/915)) ([f0098dd](https://github.com/Aureliolo/synthorg/commit/f0098dd72ec5c879111f8163bea029e665c2107a))


### Refactoring

* semantic analyzer cleanup -- shared filtering, concurrency, extraction ([#908](https://github.com/Aureliolo/synthorg/issues/908)) ([81372bf](https://github.com/Aureliolo/synthorg/commit/81372bfaec1b6f3828a608af7c6a12c25bc610cd))


### Documentation

* brand identity and UX design system from [#765](https://github.com/Aureliolo/synthorg/issues/765) exploration ([#804](https://github.com/Aureliolo/synthorg/issues/804)) ([389a9f4](https://github.com/Aureliolo/synthorg/commit/389a9f48c532be92db8980a2406aa584c2a8ab95))
* page structure and information architecture for v0.5.0 dashboard ([#809](https://github.com/Aureliolo/synthorg/issues/809)) ([f8d6d4a](https://github.com/Aureliolo/synthorg/commit/f8d6d4a140378a5c9605c160b63853d76c291dbe))
* write UX design guidelines with WCAG-verified color system ([#816](https://github.com/Aureliolo/synthorg/issues/816)) ([4a4594e](https://github.com/Aureliolo/synthorg/commit/4a4594eb035ea2ddbe2b5b9e3faa8ad748d7e7e0))


### Tests

* add unit tests for agent hooks and page components ([#875](https://github.com/Aureliolo/synthorg/issues/875)) ([#901](https://github.com/Aureliolo/synthorg/issues/901)) ([1d81546](https://github.com/Aureliolo/synthorg/commit/1d81546fbdcab3350e7d8dbcc93b2c65cfc08d6b))


### CI/CD

* bump actions/deploy-pages from 4.0.5 to 5.0.0 in the major group ([#831](https://github.com/Aureliolo/synthorg/issues/831)) ([01c19de](https://github.com/Aureliolo/synthorg/commit/01c19de66c3a5d91545fec5236f58be9e88574a3))
* bump astral-sh/setup-uv from 7.6.0 to 8.0.0 in /.github/actions/setup-python-uv in the all group ([#920](https://github.com/Aureliolo/synthorg/issues/920)) ([5f6ba54](https://github.com/Aureliolo/synthorg/commit/5f6ba54dd20f97e05ec86938bc51141d780da231))
* bump codecov/codecov-action from 5.5.3 to 6.0.0 in the major group ([#868](https://github.com/Aureliolo/synthorg/issues/868)) ([f22a181](https://github.com/Aureliolo/synthorg/commit/f22a181c721dedce569904e12211a5daa913ca56))
* bump github/codeql-action from 4.34.1 to 4.35.0 in the all group ([#883](https://github.com/Aureliolo/synthorg/issues/883)) ([87a4890](https://github.com/Aureliolo/synthorg/commit/87a4890948380d9ebfe1b502e438279f1a575b7a))
* bump sigstore/cosign-installer from 4.1.0 to 4.1.1 in the minor-and-patch group ([#830](https://github.com/Aureliolo/synthorg/issues/830)) ([7a69050](https://github.com/Aureliolo/synthorg/commit/7a69050a13ccd9475876c1414befea9073a9aacb))
* bump the all group with 3 updates ([#923](https://github.com/Aureliolo/synthorg/issues/923)) ([ff27c8e](https://github.com/Aureliolo/synthorg/commit/ff27c8e4963cd4634a8b58bb184f5b87b50ef04e))
* bump wrangler from 4.76.0 to 4.77.0 in /.github in the minor-and-patch group ([#822](https://github.com/Aureliolo/synthorg/issues/822)) ([07d43eb](https://github.com/Aureliolo/synthorg/commit/07d43eb2920e910b7adc6f5ac28ef8b55a857f7f))
* bump wrangler from 4.77.0 to 4.78.0 in /.github in the all group ([#882](https://github.com/Aureliolo/synthorg/issues/882)) ([f84118d](https://github.com/Aureliolo/synthorg/commit/f84118d119e595363518c091a8e6bf0097579ee8))


### Maintenance

* add design system enforcement hook and component inventory ([#846](https://github.com/Aureliolo/synthorg/issues/846)) ([15abc43](https://github.com/Aureliolo/synthorg/commit/15abc439f754fbd9038e3e69929ddf7254011f72))
* add dev-only auth bypass for frontend testing ([#885](https://github.com/Aureliolo/synthorg/issues/885)) ([6cdcd8a](https://github.com/Aureliolo/synthorg/commit/6cdcd8a7af5b044ef3b9eb513226c6e61e61c41f))
* add pre-push rebase check hook ([#855](https://github.com/Aureliolo/synthorg/issues/855)) ([b637a04](https://github.com/Aureliolo/synthorg/commit/b637a04dcd54399fc35fe51236a6e2a4ae17f4f6))
* backend hardening -- eviction/size-caps and model validation ([#911](https://github.com/Aureliolo/synthorg/issues/911)) ([81253d9](https://github.com/Aureliolo/synthorg/commit/81253d923699fe6c4bd45db0aca90cb51bf61444))
* bump axios from 1.13.6 to 1.14.0 in /web in the all group across 1 directory ([#922](https://github.com/Aureliolo/synthorg/issues/922)) ([b1b0232](https://github.com/Aureliolo/synthorg/commit/b1b02320e4d34b177d649333f131cedd10f7eb53))
* bump brace-expansion from 5.0.4 to 5.0.5 in /web ([#862](https://github.com/Aureliolo/synthorg/issues/862)) ([ba4a565](https://github.com/Aureliolo/synthorg/commit/ba4a565d66e62574e539d96e9569fd29a08276c1))
* bump eslint-plugin-react-refresh from 0.4.26 to 0.5.2 in /web ([#801](https://github.com/Aureliolo/synthorg/issues/801)) ([7574bb5](https://github.com/Aureliolo/synthorg/commit/7574bb500de6193b859fbad1ddb710232ca570ca))
* bump faker from 40.11.0 to 40.11.1 in the minor-and-patch group ([#803](https://github.com/Aureliolo/synthorg/issues/803)) ([14d322e](https://github.com/Aureliolo/synthorg/commit/14d322ef42ecf6d6416f5097288bc8981ea01d8b))
* bump https://github.com/astral-sh/ruff-pre-commit from v0.15.7 to 0.15.8 ([#864](https://github.com/Aureliolo/synthorg/issues/864)) ([f52901e](https://github.com/Aureliolo/synthorg/commit/f52901e40992da0d710cddaa4030494cbee6d2c8))
* bump nginxinc/nginx-unprivileged from `6582a34` to `f99cc61` in /docker/web in the all group ([#919](https://github.com/Aureliolo/synthorg/issues/919)) ([df85e4f](https://github.com/Aureliolo/synthorg/commit/df85e4fc151ad14a9c71cb84e795f12fc3ac3f21))
* bump nginxinc/nginx-unprivileged from `ccbac1a` to `6582a34` in /docker/web ([#800](https://github.com/Aureliolo/synthorg/issues/800)) ([f4e9450](https://github.com/Aureliolo/synthorg/commit/f4e94505c23d6222691ce0b0c48b62171bbe0d33))
* bump node from `44bcbf4` to `71be405` in /docker/sandbox ([#827](https://github.com/Aureliolo/synthorg/issues/827)) ([91bec67](https://github.com/Aureliolo/synthorg/commit/91bec67c1751ea6153ac955bc0d89463aa292a4f))
* bump node from `5209bca` to `cf38e1f` in /docker/web ([#863](https://github.com/Aureliolo/synthorg/issues/863)) ([66d6043](https://github.com/Aureliolo/synthorg/commit/66d60434f69c380dff0e6609e803e926bb84c2f5))
* bump picomatch in /site ([#842](https://github.com/Aureliolo/synthorg/issues/842)) ([5f20bcc](https://github.com/Aureliolo/synthorg/commit/5f20bcce4115e00fa0ec2bae68baf531bbd92321))
* bump recharts 2-&gt;3 and @types/node 22-&gt;25 in /web ([#802](https://github.com/Aureliolo/synthorg/issues/802)) ([a908800](https://github.com/Aureliolo/synthorg/commit/a90880090ae5b4ee8a7bdc1dc2a24116eb693f06))
* Bump requests from 2.32.5 to 2.33.0 ([#843](https://github.com/Aureliolo/synthorg/issues/843)) ([41daf69](https://github.com/Aureliolo/synthorg/commit/41daf6908660bded2c077d44b3a4ea187fc8ed64))
* bump smol-toml from 1.6.0 to 1.6.1 in /site ([#826](https://github.com/Aureliolo/synthorg/issues/826)) ([3e5dbe4](https://github.com/Aureliolo/synthorg/commit/3e5dbe4de5b69b377dd5f57953d86bdba3e951fc))
* bump the all group with 3 updates ([#921](https://github.com/Aureliolo/synthorg/issues/921)) ([7bace0b](https://github.com/Aureliolo/synthorg/commit/7bace0b9385707a0141a07342a645098b94004c3))
* bump the minor-and-patch group across 1 directory with 2 updates ([#829](https://github.com/Aureliolo/synthorg/issues/829)) ([93e611f](https://github.com/Aureliolo/synthorg/commit/93e611f445c296ab19ea016647181658b530e7b8))
* bump the minor-and-patch group across 1 directory with 3 updates ([#841](https://github.com/Aureliolo/synthorg/issues/841)) ([7010c8e](https://github.com/Aureliolo/synthorg/commit/7010c8e027fd0309eb8fbcbaa055cb4784f88be5))
* bump the minor-and-patch group across 1 directory with 3 updates ([#869](https://github.com/Aureliolo/synthorg/issues/869)) ([548cee5](https://github.com/Aureliolo/synthorg/commit/548cee572f406e726a599b2f1acde8a3be07be47))
* bump the minor-and-patch group in /site with 2 updates ([#865](https://github.com/Aureliolo/synthorg/issues/865)) ([9558101](https://github.com/Aureliolo/synthorg/commit/9558101be1cc05f579963d4019c711be08a2b2c2))
* bump the minor-and-patch group with 2 updates ([#867](https://github.com/Aureliolo/synthorg/issues/867)) ([4830706](https://github.com/Aureliolo/synthorg/commit/4830706ab2ed47460f7b8820f7103d85fe61229a))
* consolidate Dependabot groups to 1 PR per ecosystem ([06d2556](https://github.com/Aureliolo/synthorg/commit/06d25565c77f653a413f4ccb9e7c8d7e34c58248))
* consolidate Dependabot groups to 1 PR per ecosystem ([#881](https://github.com/Aureliolo/synthorg/issues/881)) ([06d2556](https://github.com/Aureliolo/synthorg/commit/06d25565c77f653a413f4ccb9e7c8d7e34c58248))
* improve worktree skill with full dep sync and status enhancements ([#906](https://github.com/Aureliolo/synthorg/issues/906)) ([772c625](https://github.com/Aureliolo/synthorg/commit/772c62551d0a14099908130f3dc7afabf291ad5c))
* remove Vue remnants and document framework decision ([#851](https://github.com/Aureliolo/synthorg/issues/851)) ([bf2adf6](https://github.com/Aureliolo/synthorg/commit/bf2adf6bae64ff85ae5f2383ae662435c824cb70))
* update web dependencies and fix brace-expansion CVE ([#880](https://github.com/Aureliolo/synthorg/issues/880)) ([a7a0ed6](https://github.com/Aureliolo/synthorg/commit/a7a0ed6d0c4acd2d5da04801e874b48a8409b59e))
* upgrade to Storybook 10 and TypeScript 6 ([#845](https://github.com/Aureliolo/synthorg/issues/845)) ([52d95f2](https://github.com/Aureliolo/synthorg/commit/52d95f2d570a29bcb06063f3077d3ef074016f43))

## [0.4.9](https://github.com/Aureliolo/synthorg/compare/v0.4.8...v0.4.9) (2026-03-23)


### Features

* add consultancy and data team template archetypes ([#764](https://github.com/Aureliolo/synthorg/issues/764)) ([81dc75f](https://github.com/Aureliolo/synthorg/commit/81dc75f58df87f176f2839b72227636f9c78c859))
* add personality presets for new template archetypes ([#758](https://github.com/Aureliolo/synthorg/issues/758)) ([de4e661](https://github.com/Aureliolo/synthorg/commit/de4e661f2df1b5e5c3713f2197b1fc57f967c694))
* improve wipe command UX with interactive prompts ([#759](https://github.com/Aureliolo/synthorg/issues/759)) ([bbd4d2d](https://github.com/Aureliolo/synthorg/commit/bbd4d2d0a82735840768a328f4b9e4d0e1e7e3ac))


### Bug Fixes

* stable channel detects update for dev builds ([#753](https://github.com/Aureliolo/synthorg/issues/753)) ([f53da9f](https://github.com/Aureliolo/synthorg/commit/f53da9f708f391eb4cb45dbd45a9ce553739fc3b))


### Documentation

* add version banner to docs header ([#761](https://github.com/Aureliolo/synthorg/issues/761)) ([8f8c1f8](https://github.com/Aureliolo/synthorg/commit/8f8c1f81b376fe5321f1bb3af72e2b22f17a2afc))


### Maintenance

* adopt new features from web dependency upgrades ([#763](https://github.com/Aureliolo/synthorg/issues/763)) ([1bb6336](https://github.com/Aureliolo/synthorg/commit/1bb6336112a08140bb930c8be52093c346ebde65))

## [0.4.8](https://github.com/Aureliolo/synthorg/compare/v0.4.7...v0.4.8) (2026-03-22)


### Features

* add auto_cleanup config and improve update UX ([#741](https://github.com/Aureliolo/synthorg/issues/741)) ([289638f](https://github.com/Aureliolo/synthorg/commit/289638f91e4beead51b8593a283aaf0563d9a11f))
* add reporting lines, escalation paths, and workflow handoffs to templates ([#745](https://github.com/Aureliolo/synthorg/issues/745)) ([c374cc9](https://github.com/Aureliolo/synthorg/commit/c374cc934b40fbd407a87df1b458c516050a56fc))
* differentiate template operational configs ([#742](https://github.com/Aureliolo/synthorg/issues/742)) ([9b48345](https://github.com/Aureliolo/synthorg/commit/9b4834599416bef76b0e1684ff7dab40f4fcf349))
* diversify personality preset assignments across templates ([#743](https://github.com/Aureliolo/synthorg/issues/743)) ([15487a5](https://github.com/Aureliolo/synthorg/commit/15487a5c3a23799355639cdb1cf48064a90854ba))
* improve template metadata -- skill taxonomy, descriptions, tags, and display names ([#752](https://github.com/Aureliolo/synthorg/issues/752)) ([f333f24](https://github.com/Aureliolo/synthorg/commit/f333f243182caec6cabdb42f0cf04ee4a07d0709))


### Bug Fixes

* resolve log analysis findings (Ollama prefix, logging, init) ([#748](https://github.com/Aureliolo/synthorg/issues/748)) ([8f871a4](https://github.com/Aureliolo/synthorg/commit/8f871a4b975db603fa105afb48c713c9a43769ec))
* use git tag for dev release container image tags ([#749](https://github.com/Aureliolo/synthorg/issues/749)) ([f30d071](https://github.com/Aureliolo/synthorg/commit/f30d071c2f2ef017dc78aa2f6566150f326c66d0))
* use subordinate_id/supervisor_id in HierarchyResolver ([#751](https://github.com/Aureliolo/synthorg/issues/751)) ([118235b](https://github.com/Aureliolo/synthorg/commit/118235b9ce6867e5eb36a9e1db90529d4d42e156))


### Performance

* add long-lived cache headers for content-hashed static assets ([#747](https://github.com/Aureliolo/synthorg/issues/747)) ([4d350b5](https://github.com/Aureliolo/synthorg/commit/4d350b55347227a08670c07b1242cacb46a4883a))
* use worksteal distribution for pytest-xdist ([#750](https://github.com/Aureliolo/synthorg/issues/750)) ([b7dd7de](https://github.com/Aureliolo/synthorg/commit/b7dd7de30c0b762c3c9f1ea4fbeb3884783a3d2a))

## [0.4.7](https://github.com/Aureliolo/synthorg/compare/v0.4.6...v0.4.7) (2026-03-22)


### Features

* add system user for CLI-to-backend authentication ([#710](https://github.com/Aureliolo/synthorg/issues/710)) ([dc6bd3f](https://github.com/Aureliolo/synthorg/commit/dc6bd3f0c9faa733862fc6079cddedf26aad4eb8))
* dev channel builds with incremental pre-releases between stable releases ([#715](https://github.com/Aureliolo/synthorg/issues/715)) ([0e8a714](https://github.com/Aureliolo/synthorg/commit/0e8a7141cb0cd23a7b9ec00c8b992f7f74545ffd))
* replace hardcoded name pools with Faker multi-locale name generation ([#714](https://github.com/Aureliolo/synthorg/issues/714)) ([5edc6ec](https://github.com/Aureliolo/synthorg/commit/5edc6ecbb22d9daff50958bf531cb5d5cb99a7cb))


### Bug Fixes

* dev-release tag creation, dependabot coverage, go -C cli convention ([#730](https://github.com/Aureliolo/synthorg/issues/730)) ([7634843](https://github.com/Aureliolo/synthorg/commit/763484398c7212788a82c82b53c367a122cd3cce))
* improve name generation step UX and fix sentinel expansion bug ([#739](https://github.com/Aureliolo/synthorg/issues/739)) ([f03fd05](https://github.com/Aureliolo/synthorg/commit/f03fd050687e35bd53449c53cd012da0e1185e0b))
* settings page UX polish -- toggle bug, source badges, form improvements ([#712](https://github.com/Aureliolo/synthorg/issues/712)) ([d16a0ac](https://github.com/Aureliolo/synthorg/commit/d16a0acec025b8abf07d30d748363ce1baed4aed))
* switch dev tags to semver and use same release pipeline as stable ([#729](https://github.com/Aureliolo/synthorg/issues/729)) ([4df6b9b](https://github.com/Aureliolo/synthorg/commit/4df6b9b4513245f618103fa89e42ee3a250bc269)), closes [#713](https://github.com/Aureliolo/synthorg/issues/713)
* unify CLI image discovery and standardize Go tooling ([#738](https://github.com/Aureliolo/synthorg/issues/738)) ([712a785](https://github.com/Aureliolo/synthorg/commit/712a785a14f416d9892ea7a8ab26502e45f617e4))
* use PAT in dev-release workflow to trigger downstream pipelines ([#716](https://github.com/Aureliolo/synthorg/issues/716)) ([d767aa3](https://github.com/Aureliolo/synthorg/commit/d767aa38366745d1af8bdc3505759df36ca5412a))


### CI/CD

* bump astral-sh/setup-uv from 7.4.0 to 7.6.0 in /.github/actions/setup-python-uv in the minor-and-patch group ([#731](https://github.com/Aureliolo/synthorg/issues/731)) ([7887257](https://github.com/Aureliolo/synthorg/commit/7887257de38168238ff0f11dda4a38b6abdec554))
* bump the minor-and-patch group with 3 updates ([#735](https://github.com/Aureliolo/synthorg/issues/735)) ([7cd253a](https://github.com/Aureliolo/synthorg/commit/7cd253a8910e7bb23529ef2fae59495871a797ec))
* bump wrangler from 4.75.0 to 4.76.0 in /.github in the minor-and-patch group ([#732](https://github.com/Aureliolo/synthorg/issues/732)) ([a6cafc7](https://github.com/Aureliolo/synthorg/commit/a6cafc782ec0ac242adf7ee46e091be9a90b9670))
* clean up all dev releases and tags on stable release ([#737](https://github.com/Aureliolo/synthorg/issues/737)) ([8d90f5c](https://github.com/Aureliolo/synthorg/commit/8d90f5ca2f6092316fc096b3eae25c6376c3be09))


### Maintenance

* bump the minor-and-patch group across 2 directories with 2 updates ([#733](https://github.com/Aureliolo/synthorg/issues/733)) ([2b60069](https://github.com/Aureliolo/synthorg/commit/2b60069da2d83133507c3442af5dfdc8baf8ff01))
* bump the minor-and-patch group with 3 updates ([#734](https://github.com/Aureliolo/synthorg/issues/734)) ([859bc25](https://github.com/Aureliolo/synthorg/commit/859bc25ec1101ef309e7c327ccb7d058ea13d278))
* fix dependabot labels and add scope tags ([#736](https://github.com/Aureliolo/synthorg/issues/736)) ([677eb15](https://github.com/Aureliolo/synthorg/commit/677eb15fdb1a1165eefcfdbf884bd31d47548511))
* remove redundant pytest.mark.timeout(30) markers ([#740](https://github.com/Aureliolo/synthorg/issues/740)) ([9ec2163](https://github.com/Aureliolo/synthorg/commit/9ec2163e4ee29a2ea4f92ed1dc0e30097435b96d))

## [0.4.6](https://github.com/Aureliolo/synthorg/compare/v0.4.5...v0.4.6) (2026-03-22)


### Features

* dynamic SSRF allowlist for provider discovery ([#684](https://github.com/Aureliolo/synthorg/issues/684)) ([be235b8](https://github.com/Aureliolo/synthorg/commit/be235b8a6f9d3a84facf414bb7c544f96a3f63e6))
* overhaul settings page with Company/Providers extraction, grid layout, and CodeMirror editor ([#683](https://github.com/Aureliolo/synthorg/issues/683)) ([1974184](https://github.com/Aureliolo/synthorg/commit/19741846a5bae5db7bc55df4081c8da1517f458d))
* replace setup command with wipe for full factory reset ([#682](https://github.com/Aureliolo/synthorg/issues/682)) ([c0e12cc](https://github.com/Aureliolo/synthorg/commit/c0e12cced06247ff1c517157709df469d0f74d86))


### Bug Fixes

* auto-wire meeting orchestrator and scheduler ([#669](https://github.com/Aureliolo/synthorg/issues/669)) ([#680](https://github.com/Aureliolo/synthorg/issues/680)) ([8900df5](https://github.com/Aureliolo/synthorg/commit/8900df5fa77f5493b3f1e4d894e389eef7722070))
* move healthchecks to Dockerfiles, remove compose overrides ([#678](https://github.com/Aureliolo/synthorg/issues/678)) ([f0a7a0e](https://github.com/Aureliolo/synthorg/commit/f0a7a0e0d2aa27db5c67a25f79856c51aa68814b))
* resolve port 8000 collision between vLLM preset and backend ([#681](https://github.com/Aureliolo/synthorg/issues/681)) ([ed59ff9](https://github.com/Aureliolo/synthorg/commit/ed59ff97bf67f32a2ac7e8a646787ffa6813ad4c))

## [0.4.5](https://github.com/Aureliolo/synthorg/compare/v0.4.4...v0.4.5) (2026-03-21)


### Bug Fixes

* add empty state, fitView safety net, and node interactivity to org chart ([#677](https://github.com/Aureliolo/synthorg/issues/677)) ([07a92da](https://github.com/Aureliolo/synthorg/commit/07a92da575e608e5fdc156c4edb81925cae2a76a))
* live pull progress box, sandbox healthcheck, doctor summary ([#668](https://github.com/Aureliolo/synthorg/issues/668)) ([67047bd](https://github.com/Aureliolo/synthorg/commit/67047bdf70aabf5dc346ae1e42b8399c8945c91a))
* replace manual agent hiring with template auto-creation in setup wizard ([#672](https://github.com/Aureliolo/synthorg/issues/672)) ([7a16607](https://github.com/Aureliolo/synthorg/commit/7a166073f4939549028a5baefc518cda610c95e7))
* resolve SSRF trust bug, add log sinks, fix Pydantic warning ([#673](https://github.com/Aureliolo/synthorg/issues/673)) ([e6a18cc](https://github.com/Aureliolo/synthorg/commit/e6a18ccbe1be0ff3b4e8b87ca5738105aebcf6ca))

## [0.4.4](https://github.com/Aureliolo/synthorg/compare/v0.4.3...v0.4.4) (2026-03-21)


### Features

* enforce custom security policies and sandbox allowed_hosts ([#664](https://github.com/Aureliolo/synthorg/issues/664)) ([71d8839](https://github.com/Aureliolo/synthorg/commit/71d88396b6fd22810ace4ff10c4cfbea746caac5))


### Bug Fixes

* overhaul CLI output, fix setup 401, add cleanup command ([#663](https://github.com/Aureliolo/synthorg/issues/663)) ([fb01c78](https://github.com/Aureliolo/synthorg/commit/fb01c78589f4e3fc1ff8263e2b065ac5346b8b0d))
* remediate flaky test patterns and add missing test coverage ([#662](https://github.com/Aureliolo/synthorg/issues/662)) ([bbc94e5](https://github.com/Aureliolo/synthorg/commit/bbc94e5c33e12ecaf9a648e36e961cd1d74768ee))
* use PrimeVue native Password component for all secret fields ([#661](https://github.com/Aureliolo/synthorg/issues/661)) ([926cb15](https://github.com/Aureliolo/synthorg/commit/926cb1581c56b3718053ad81146553ce4c4f744a))


### Performance

* remove DAST scan from push-to-main trigger ([#665](https://github.com/Aureliolo/synthorg/issues/665)) ([3135b0f](https://github.com/Aureliolo/synthorg/commit/3135b0ff453f603d82535074f676228e79283167))


### Documentation

* add WIP banner, clean up roadmap, remove stale placeholders ([#667](https://github.com/Aureliolo/synthorg/issues/667)) ([194f637](https://github.com/Aureliolo/synthorg/commit/194f637be447ce9b4d5bace612591230f7bbab73))
* fix incorrect commands, vendor name leaks, and site gaps ([#660](https://github.com/Aureliolo/synthorg/issues/660)) ([b0f7a23](https://github.com/Aureliolo/synthorg/commit/b0f7a235070ad398e95004028936141fe528e693))


### Maintenance

* replace em-dashes with ASCII dashes and add prevention hook ([#659](https://github.com/Aureliolo/synthorg/issues/659)) ([cf0a500](https://github.com/Aureliolo/synthorg/commit/cf0a500e4ff854b47a63ff0ed7a9382cb469bda4))
* trim CLAUDE.md from 40.2k to 20.5k chars ([#657](https://github.com/Aureliolo/synthorg/issues/657)) ([db5223f](https://github.com/Aureliolo/synthorg/commit/db5223fe123a61df95c849f1a43f3c0813943f86))

## [0.4.3](https://github.com/Aureliolo/synthorg/compare/v0.4.2...v0.4.3) (2026-03-21)


### Bug Fixes

* harden third-party logger taming with proper cleanup and broader coverage ([#656](https://github.com/Aureliolo/synthorg/issues/656)) ([6d9874d](https://github.com/Aureliolo/synthorg/commit/6d9874d8acd10537036940cdee84db5e2ac3afa2))
* setup wizard -- password toggle, stepper, discovery-as-test ([#655](https://github.com/Aureliolo/synthorg/issues/655)) ([54dd199](https://github.com/Aureliolo/synthorg/commit/54dd19947ed96c0503d6c01935573354ab0893a0))


### Maintenance

* bump h3 from 1.15.6 to 1.15.9 in /site ([#653](https://github.com/Aureliolo/synthorg/issues/653)) ([b184ee2](https://github.com/Aureliolo/synthorg/commit/b184ee2b6a579337422aa6eaeca9147100b9ce93))

## [0.4.2](https://github.com/Aureliolo/synthorg/compare/v0.4.1...v0.4.2) (2026-03-20)


### Features

* implement LLM fallback for uncertain security evaluations ([#647](https://github.com/Aureliolo/synthorg/issues/647)) ([d13f6c9](https://github.com/Aureliolo/synthorg/commit/d13f6c9c77178fcd5525fab10dece63d9029175c))
* implement quota degradation FALLBACK and QUEUE strategies ([#650](https://github.com/Aureliolo/synthorg/issues/650)) ([5828051](https://github.com/Aureliolo/synthorg/commit/582805184ca923b2f87793140b0f3a60b0919e88))


### Bug Fixes

* CLI falsely reports missing images after update ([#648](https://github.com/Aureliolo/synthorg/issues/648)) ([aeb4680](https://github.com/Aureliolo/synthorg/commit/aeb4680dd643c507d25184febcfbb5bd2e677bdc))
* observability sink routing gaps and agent correlation binding ([#646](https://github.com/Aureliolo/synthorg/issues/646)) ([9bb56eb](https://github.com/Aureliolo/synthorg/commit/9bb56eb36f54bca69c755e4409d5ba7279727c83))
* setup wizard UX issues (step indicator, discovery, auth, toggle) ([#651](https://github.com/Aureliolo/synthorg/issues/651)) ([2f58421](https://github.com/Aureliolo/synthorg/commit/2f58421e52d404faf15bd8c0ca1314bbe9f721ef))


### Maintenance

* silence false-positive ESLint security warnings ([#649](https://github.com/Aureliolo/synthorg/issues/649)) ([e2dc171](https://github.com/Aureliolo/synthorg/commit/e2dc1718d23df2aeb7652a027d082c3d6be6bc05))

## [0.4.1](https://github.com/Aureliolo/synthorg/compare/v0.4.0...v0.4.1) (2026-03-20)


### Bug Fixes

* CLI update resilience + setup wizard UX overhaul ([#642](https://github.com/Aureliolo/synthorg/issues/642)) ([774e4e4](https://github.com/Aureliolo/synthorg/commit/774e4e461a832c6bad1d04a56aeaea75262b7856))

## [0.4.0](https://github.com/Aureliolo/synthorg/compare/v0.3.10...v0.4.0) (2026-03-20)


### Bug Fixes

* add WebSocket reconnect feedback and fix setup provider validation ([#636](https://github.com/Aureliolo/synthorg/issues/636)) ([77f14f0](https://github.com/Aureliolo/synthorg/commit/77f14f09f70a731dd8c7e09dd7468cc50040eb47))
* **api:** controller consistency, input validation, and auth rate limiting ([#638](https://github.com/Aureliolo/synthorg/issues/638)) ([1192a60](https://github.com/Aureliolo/synthorg/commit/1192a60b4633991fa84a314560983277d78fc143))
* **cli:** handle ignored errors in version parsing and flag reading ([#635](https://github.com/Aureliolo/synthorg/issues/635)) ([cfd7ce4](https://github.com/Aureliolo/synthorg/commit/cfd7ce4b41c8a3ceaf25c9cfda5382f11d81a2a5))
* prevent Litestar from destroying structlog file sinks and add provider auto-probe ([#639](https://github.com/Aureliolo/synthorg/issues/639)) ([476ee5f](https://github.com/Aureliolo/synthorg/commit/476ee5fc602556d0badfa0fb323dffd85228c838))


### Documentation

* add releasing instructions to CLAUDE.md ([#640](https://github.com/Aureliolo/synthorg/issues/640)) ([2e08ca4](https://github.com/Aureliolo/synthorg/commit/2e08ca4837a16695cc8aaebc1700503820aef9dc))

## [0.3.10](https://github.com/Aureliolo/synthorg/compare/v0.3.9...v0.3.10) (2026-03-20)


### Bug Fixes

* **ci:** generate required secrets in DAST workflow ([#623](https://github.com/Aureliolo/synthorg/issues/623)) ([6ae297f](https://github.com/Aureliolo/synthorg/commit/6ae297f881ba102cf73dd59f5b1b5bd723008279))
* **cli:** doctor image check reads compose file and fix API docs URL ([#625](https://github.com/Aureliolo/synthorg/issues/625)) ([5202e53](https://github.com/Aureliolo/synthorg/commit/5202e53771d6210712fb5e8f581bca3dee0e6ece))
* **engine:** sanitize error messages in checkpoint reconciliation and compaction summaries ([#632](https://github.com/Aureliolo/synthorg/issues/632)) ([5394ed7](https://github.com/Aureliolo/synthorg/commit/5394ed72f78a2b5439c98e25144d69005d36cea6))
* mitigate TOCTOU DNS rebinding gap in git clone SSRF prevention ([#633](https://github.com/Aureliolo/synthorg/issues/633)) ([1846f6e](https://github.com/Aureliolo/synthorg/commit/1846f6eef0650968e19b97c4c66002d04104006e))
* resolve post-startup log loss, add provider model discovery, and improve setup wizard UX ([#634](https://github.com/Aureliolo/synthorg/issues/634)) ([2df8d11](https://github.com/Aureliolo/synthorg/commit/2df8d1137caebbfca0e9819c33a29a867821e5c1))


### Maintenance

* bump https://github.com/astral-sh/ruff-pre-commit from v0.15.6 to 0.15.7 ([#628](https://github.com/Aureliolo/synthorg/issues/628)) ([c641d2c](https://github.com/Aureliolo/synthorg/commit/c641d2c35e0e8f43e2cc3f21253b656a580441f1))
* bump python from `584e89d` to `fb83750` in /docker/backend ([#627](https://github.com/Aureliolo/synthorg/issues/627)) ([1a36eca](https://github.com/Aureliolo/synthorg/commit/1a36ecab3783108b27c1d58038fb3cfee8671ac9))
* bump python from `584e89d` to `fb83750` in /docker/sandbox ([#629](https://github.com/Aureliolo/synthorg/issues/629)) ([fd3e69a](https://github.com/Aureliolo/synthorg/commit/fd3e69aa0ec8c8d43d5660d270b13372f81a4109))
* bump the minor-and-patch group across 2 directories with 3 updates ([#630](https://github.com/Aureliolo/synthorg/issues/630)) ([67d14c4](https://github.com/Aureliolo/synthorg/commit/67d14c4f95f9ce3621254ea2c214ae31a5949349))
* bump the minor-and-patch group with 2 updates ([#631](https://github.com/Aureliolo/synthorg/issues/631)) ([2e51b60](https://github.com/Aureliolo/synthorg/commit/2e51b60e9bdd2cedb7631c28aad38bc381303b13))
* **ci:** add timeout-minutes, harden fuzz script, extend CVE audit ([#626](https://github.com/Aureliolo/synthorg/issues/626)) ([25420e2](https://github.com/Aureliolo/synthorg/commit/25420e2714e5f4d94d78b877d16d4653a462dc47))

## [0.3.9](https://github.com/Aureliolo/synthorg/compare/v0.3.8...v0.3.9) (2026-03-19)


### Features

* add company description field to setup wizard ([#617](https://github.com/Aureliolo/synthorg/issues/617)) ([7c43693](https://github.com/Aureliolo/synthorg/commit/7c43693c8db1dd651760abe320ca416d576dcd5a))
* implement approval review gate and timeout scheduler ([#620](https://github.com/Aureliolo/synthorg/issues/620)) ([229d366](https://github.com/Aureliolo/synthorg/commit/229d366e9248b3afcbe3f238b9ef7910618976b5))


### Bug Fixes

* generate settings encryption key on init and flush log file handlers ([#621](https://github.com/Aureliolo/synthorg/issues/621)) ([60c5744](https://github.com/Aureliolo/synthorg/commit/60c574448c6bbb1e06761111f8eaae87c7c4462d))


### Maintenance

* remove premature versioning, migrations, and backward-compat scaffolding ([#618](https://github.com/Aureliolo/synthorg/issues/618)) ([5f6550b](https://github.com/Aureliolo/synthorg/commit/5f6550b1f9d41e97af50e121717dabde9bf82c72))

## [0.3.8](https://github.com/Aureliolo/synthorg/compare/v0.3.7...v0.3.8) (2026-03-19)


### Features

* dynamic settings UI with auto-discovery and basic/advanced toggle ([#600](https://github.com/Aureliolo/synthorg/issues/600)) ([11b32b7](https://github.com/Aureliolo/synthorg/commit/11b32b7b486e7a9145cb14a63067d04b63150235))


### Bug Fixes

* **ci:** remove dst: . from GoReleaser archive config ([#598](https://github.com/Aureliolo/synthorg/issues/598)) ([c8bf862](https://github.com/Aureliolo/synthorg/commit/c8bf862ae4a17680911241b2f543628054cfe9c4))
* **engine:** wire compaction_callback and plan_execute_config through auto-selection ([#601](https://github.com/Aureliolo/synthorg/issues/601)) ([795327f](https://github.com/Aureliolo/synthorg/commit/795327f33364d276c723aea1dc5ff93f4fa0226e))
* harden setup wizard completion and status checks ([#616](https://github.com/Aureliolo/synthorg/issues/616)) ([d99d7b7](https://github.com/Aureliolo/synthorg/commit/d99d7b7f22de60113fda657ab7fe39e83dcbd0ba))


### Maintenance

* add /codebase-audit skill for deep parallel codebase auditing ([#613](https://github.com/Aureliolo/synthorg/issues/613)) ([db02320](https://github.com/Aureliolo/synthorg/commit/db0232081b0c24a61784538d430a84167587f023))

## [0.3.7](https://github.com/Aureliolo/synthorg/compare/v0.3.6...v0.3.7) (2026-03-19)


### Features

* **engine:** implement Hybrid Plan + ReAct execution loop ([#582](https://github.com/Aureliolo/synthorg/issues/582)) ([008147c](https://github.com/Aureliolo/synthorg/commit/008147c698d3443c95618be4d783a5d3d3813005))
* implement first-run setup wizard ([#584](https://github.com/Aureliolo/synthorg/issues/584)) ([dfed931](https://github.com/Aureliolo/synthorg/commit/dfed93123bfd24fabc50bc52d46211343835efea))


### Bug Fixes

* **api:** address ZAP DAST scan findings ([#579](https://github.com/Aureliolo/synthorg/issues/579)) ([ce9a3e0](https://github.com/Aureliolo/synthorg/commit/ce9a3e077ab6af5743a8150333d225d0de9ab0d3))
* **ci:** remove CLI SBOM generation, reset failed v0.3.7 ([#595](https://github.com/Aureliolo/synthorg/issues/595)) ([d0f4992](https://github.com/Aureliolo/synthorg/commit/d0f4992e3a495e70ffeb43d1e1af6cdf6ba78130))
* **ci:** reset failed v0.3.7 release and fix syft SBOM scan ([#593](https://github.com/Aureliolo/synthorg/issues/593)) ([d1508c2](https://github.com/Aureliolo/synthorg/commit/d1508c2f1414d5e9a7dd07753581038584febee0))
* **cli:** auto-delete binary on Windows, prune images, fix GoReleaser ([#590](https://github.com/Aureliolo/synthorg/issues/590)) ([eb7c691](https://github.com/Aureliolo/synthorg/commit/eb7c691c333f36f2123fea68a8a4d9637111442d))
* **cli:** regenerate compose and re-exec binary on update ([#576](https://github.com/Aureliolo/synthorg/issues/576)) ([3f226eb](https://github.com/Aureliolo/synthorg/commit/3f226eb79b46de59c1e94319a046765353392de4))


### CI/CD

* add SBOM generation to Docker and CLI releases ([#580](https://github.com/Aureliolo/synthorg/issues/580)) ([db459cf](https://github.com/Aureliolo/synthorg/commit/db459cf0892c46f9a887126edd70aeaafe6b70d8))


### Maintenance

* **main:** release 0.3.7 ([#583](https://github.com/Aureliolo/synthorg/issues/583)) ([bf58779](https://github.com/Aureliolo/synthorg/commit/bf587792d8a5fb5508fb373d45e01648dc732dec))
* **main:** release 0.3.7 ([#592](https://github.com/Aureliolo/synthorg/issues/592)) ([2e8e633](https://github.com/Aureliolo/synthorg/commit/2e8e633afee489f4288f09a86580e50eb1e32d3f))
* **main:** release 0.3.7 ([#594](https://github.com/Aureliolo/synthorg/issues/594)) ([139dfc1](https://github.com/Aureliolo/synthorg/commit/139dfc1ab4d0cee3dc2b5d8be1a473761011fc8f))
* reset failed v0.3.7 release ([#591](https://github.com/Aureliolo/synthorg/issues/591)) ([b69000d](https://github.com/Aureliolo/synthorg/commit/b69000da37ae9092ff59698a15cf10abd3d06ebb))

## [0.3.6](https://github.com/Aureliolo/synthorg/compare/v0.3.5...v0.3.6) (2026-03-19)


### Features

* **cli:** add backup subcommands (backup, backup list, backup restore) ([#568](https://github.com/Aureliolo/synthorg/issues/568)) ([4c06b1d](https://github.com/Aureliolo/synthorg/commit/4c06b1d6bbe21a45cb7a83591afc824c4cc7b9a8))
* **engine:** implement execution loop auto-selection based on task complexity ([#567](https://github.com/Aureliolo/synthorg/issues/567)) ([5bfc2c6](https://github.com/Aureliolo/synthorg/commit/5bfc2c6b9bb8f2909596a3336f6056de81b8cb2b))


### Bug Fixes

* activate structured logging pipeline -- wire 8-sink system, integrate Uvicorn, suppress spam ([#572](https://github.com/Aureliolo/synthorg/issues/572)) ([9b6bf33](https://github.com/Aureliolo/synthorg/commit/9b6bf332954984d099c5967768581d70534a379d))
* **cli:** bump grpc-go v1.79.3 -- CVE-2026-33186 auth bypass ([#574](https://github.com/Aureliolo/synthorg/issues/574)) ([f0171c9](https://github.com/Aureliolo/synthorg/commit/f0171c932d4fa8fa32ebae71988ddeded82b4b9a))
* resolve OpenAPI schema validation warnings for union/optional fields ([#558](https://github.com/Aureliolo/synthorg/issues/558)) ([5d96b2b](https://github.com/Aureliolo/synthorg/commit/5d96b2bdb4cec91b762a97820e0100324e7651fc))


### CI/CD

* bump codecov/codecov-action from 5.5.2 to 5.5.3 in the minor-and-patch group ([#571](https://github.com/Aureliolo/synthorg/issues/571)) ([267f685](https://github.com/Aureliolo/synthorg/commit/267f6858d999a0653047dd57a196907fafd1a0d8))
* ignore chainguard/python in Dependabot docker updates ([#575](https://github.com/Aureliolo/synthorg/issues/575)) ([1935eaa](https://github.com/Aureliolo/synthorg/commit/1935eaaf357d7b01ff5c47903be7e3aa7f7ff79f))


### Maintenance

* bump the major group across 1 directory with 2 updates ([#570](https://github.com/Aureliolo/synthorg/issues/570)) ([b98f82c](https://github.com/Aureliolo/synthorg/commit/b98f82c55e1375fd3ffafbd1b941f62dbc8b49d6))
* bump the minor-and-patch group across 2 directories with 4 updates ([#569](https://github.com/Aureliolo/synthorg/issues/569)) ([3295168](https://github.com/Aureliolo/synthorg/commit/32951688f1e8b7f715ab1f0bf6bf33cf8d23b8f0))

## [0.3.5](https://github.com/Aureliolo/synthorg/compare/v0.3.4...v0.3.5) (2026-03-18)


### Features

* **api:** auto-wire backend services at startup ([#555](https://github.com/Aureliolo/synthorg/issues/555)) ([0e52c47](https://github.com/Aureliolo/synthorg/commit/0e52c471c43ea24392f0329f64580577b9c0cfff))


### Bug Fixes

* **api:** resolve WebSocket 403 rejection ([#549](https://github.com/Aureliolo/synthorg/issues/549)) ([#556](https://github.com/Aureliolo/synthorg/issues/556)) ([60453d2](https://github.com/Aureliolo/synthorg/commit/60453d2aca0b3d0a9715bcc479209c2a3b54b9c4))
* **cli:** verify SLSA provenance via GitHub attestation API ([#548](https://github.com/Aureliolo/synthorg/issues/548)) ([91d4f79](https://github.com/Aureliolo/synthorg/commit/91d4f79eb946825ef4b0bffd133353e292a4cadb)), closes [#532](https://github.com/Aureliolo/synthorg/issues/532)


### Performance

* **test:** speed up test suite -- reduce Hypothesis examples and eliminate real sleeps ([#557](https://github.com/Aureliolo/synthorg/issues/557)) ([d5f3a41](https://github.com/Aureliolo/synthorg/commit/d5f3a411b48db3e69e6be822afc19e64c27d0ba8))


### Refactoring

* replace _ErrorResponseSpec NamedTuple with TypedDict ([#554](https://github.com/Aureliolo/synthorg/issues/554)) ([71cc6e1](https://github.com/Aureliolo/synthorg/commit/71cc6e10ef12fe2ab064b16bfccf8d9861d966c1))


### Maintenance

* **docker:** suppress pydantic v1 warning on Python 3.14 ([#552](https://github.com/Aureliolo/synthorg/issues/552)) ([cbe1f05](https://github.com/Aureliolo/synthorg/commit/cbe1f051ae36c4d6f6601444324f40309266e83c)), closes [#551](https://github.com/Aureliolo/synthorg/issues/551)

## [0.3.4](https://github.com/Aureliolo/synthorg/compare/v0.3.3...v0.3.4) (2026-03-18)


### Bug Fixes

* **cli:** support cosign v3 bundle format for signature verification ([#546](https://github.com/Aureliolo/synthorg/issues/546)) ([6115eff](https://github.com/Aureliolo/synthorg/commit/6115eff5de14b5a18ebbe789f8a77d21f38e7f8c)), closes [#532](https://github.com/Aureliolo/synthorg/issues/532)

## [0.3.3](https://github.com/Aureliolo/synthorg/compare/v0.3.2...v0.3.3) (2026-03-18)


### Features

* **backup:** implement automated backup and restore system ([#541](https://github.com/Aureliolo/synthorg/issues/541)) ([867b7c1](https://github.com/Aureliolo/synthorg/commit/867b7c1b473a4da7472e6982cc971de9fffd9416))
* **providers:** runtime provider management with CRUD, presets, and multi-auth ([#540](https://github.com/Aureliolo/synthorg/issues/540)) ([936c345](https://github.com/Aureliolo/synthorg/commit/936c3455f635fe72da87230ae3d57f18a889f36e)), closes [#451](https://github.com/Aureliolo/synthorg/issues/451)
* **tools:** wire per-category sandbox backend selection ([#534](https://github.com/Aureliolo/synthorg/issues/534)) ([311a1ab](https://github.com/Aureliolo/synthorg/commit/311a1abd6978ecbb268b844ab711bd0e199c6dc3))


### Bug Fixes

* **ci:** add COSIGN_EXPERIMENTAL=1 for OCI referrer mode in cosign sign ([#543](https://github.com/Aureliolo/synthorg/issues/543)) ([226ed2f](https://github.com/Aureliolo/synthorg/commit/226ed2f3b976cd95cc96bf58be0eafc5581189a1))
* **cli:** switch cosign verification from .sig tags to OCI referrers ([#533](https://github.com/Aureliolo/synthorg/issues/533)) ([8ee5471](https://github.com/Aureliolo/synthorg/commit/8ee547140ab9c06c5ed900f2b3171fc2bbc26572)), closes [#532](https://github.com/Aureliolo/synthorg/issues/532)


### CI/CD

* bump wrangler from 4.74.0 to 4.75.0 in /.github in the minor-and-patch group ([#535](https://github.com/Aureliolo/synthorg/issues/535)) ([de15867](https://github.com/Aureliolo/synthorg/commit/de158670810dd25e2fdceb3191af302e5c299245))


### Maintenance

* bump github.com/google/go-containerregistry from 0.21.2 to 0.21.3 in /cli in the minor-and-patch group ([#536](https://github.com/Aureliolo/synthorg/issues/536)) ([4a09aed](https://github.com/Aureliolo/synthorg/commit/4a09aed16e4ec0ef557690041f8b35fa44a3a733))
* bump litellm from 1.82.3 to 1.82.4 in the minor-and-patch group ([#538](https://github.com/Aureliolo/synthorg/issues/538)) ([9f7f83d](https://github.com/Aureliolo/synthorg/commit/9f7f83d8cf31d44ae8f08108b64e60857785dbd3))
* bump vue-tsc from 3.2.5 to 3.2.6 in /web in the minor-and-patch group across 1 directory ([#537](https://github.com/Aureliolo/synthorg/issues/537)) ([eb3dc4e](https://github.com/Aureliolo/synthorg/commit/eb3dc4ea7e46e62729bab26a87a2ed849e09f564))
* **main:** release 0.3.3 ([#539](https://github.com/Aureliolo/synthorg/issues/539)) ([c3de2a2](https://github.com/Aureliolo/synthorg/commit/c3de2a24c3612a6d4f7f3cd3932a53507472530f))
* revert v0.3.3 release artifacts (Docker signing failed) ([#544](https://github.com/Aureliolo/synthorg/issues/544)) ([7f48f52](https://github.com/Aureliolo/synthorg/commit/7f48f5262a7124ae79d51a4e4dd523d18b42d93a))

## [0.3.2](https://github.com/Aureliolo/synthorg/compare/v0.3.1...v0.3.2) (2026-03-17)


### Features

* **settings:** route structural data reads through SettingsService ([#525](https://github.com/Aureliolo/synthorg/issues/525)) ([289f604](https://github.com/Aureliolo/synthorg/commit/289f6047681fa8b4eecd227e317360a4f32bf0d4))


### Bug Fixes

* **cli:** add fallback arch detection in PowerShell installer ([#529](https://github.com/Aureliolo/synthorg/issues/529)) ([0250afb](https://github.com/Aureliolo/synthorg/commit/0250afbf5aabca8ba8e2e9ca2b01cc8fff3bdf8a)), closes [#521](https://github.com/Aureliolo/synthorg/issues/521)


### CI/CD

* bump the minor-and-patch group with 2 updates ([#517](https://github.com/Aureliolo/synthorg/issues/517)) ([46bdd1a](https://github.com/Aureliolo/synthorg/commit/46bdd1a49080ac1127aa99b7729edc92bc9ab195))
* bump wrangler from 4.73.0 to 4.74.0 in /.github in the minor-and-patch group ([#511](https://github.com/Aureliolo/synthorg/issues/511)) ([903b71a](https://github.com/Aureliolo/synthorg/commit/903b71ae73fd0b7d7642eb17958e6763141478c5))


### Maintenance

* bump node from `7a4ef57` to `44bcbf4` in /docker/sandbox ([#515](https://github.com/Aureliolo/synthorg/issues/515)) ([3cbddd1](https://github.com/Aureliolo/synthorg/commit/3cbddd1ce4ef665f20aa3be9d5a67786d7ff7364))
* bump python from `6a27522` to `584e89d` in /docker/backend ([#513](https://github.com/Aureliolo/synthorg/issues/513)) ([0715910](https://github.com/Aureliolo/synthorg/commit/0715910445ea5db5b924b69c985fee6e3f9a236c))
* bump python from `6a27522` to `584e89d` in /docker/sandbox ([#514](https://github.com/Aureliolo/synthorg/issues/514)) ([787dfe1](https://github.com/Aureliolo/synthorg/commit/787dfe1cbfa86b3de63a84e4888e6508402660bd))
* bump the minor-and-patch group across 1 directory with 2 updates ([#527](https://github.com/Aureliolo/synthorg/issues/527)) ([e96c0d4](https://github.com/Aureliolo/synthorg/commit/e96c0d44879aa91f374c969c9522342ca4dd3384))
* bump the minor-and-patch group across 2 directories with 3 updates ([#512](https://github.com/Aureliolo/synthorg/issues/512)) ([b95ba3d](https://github.com/Aureliolo/synthorg/commit/b95ba3da089c97e137de29e0b287239809db7960))
* **docker:** disable Mem0 telemetry in container config ([#531](https://github.com/Aureliolo/synthorg/issues/531)) ([9fc29eb](https://github.com/Aureliolo/synthorg/commit/9fc29ebeb8c24e88153a8c9abd54f9772dd79afd))
* improve GitHub issue templates with structured forms ([#528](https://github.com/Aureliolo/synthorg/issues/528)) ([4fb66cf](https://github.com/Aureliolo/synthorg/commit/4fb66cf45d59523b21461b1ffe0820abac34acfe)), closes [#522](https://github.com/Aureliolo/synthorg/issues/522)

## [0.3.1](https://github.com/Aureliolo/synthorg/compare/v0.3.0...v0.3.1) (2026-03-17)


### Features

* **api:** RFC 9457 Phase 2 -- ProblemDetail and content negotiation ([#496](https://github.com/Aureliolo/synthorg/issues/496)) ([30f7c49](https://github.com/Aureliolo/synthorg/commit/30f7c49ff2562919988ed510abd805ba3752ae92))
* **cli:** verify container image signatures and SLSA provenance on pull ([#492](https://github.com/Aureliolo/synthorg/issues/492)) ([bef272d](https://github.com/Aureliolo/synthorg/commit/bef272d37e0020e9f33da2ce611d33cf749d570f)), closes [#491](https://github.com/Aureliolo/synthorg/issues/491)
* **engine:** implement context budget management in execution loops ([#520](https://github.com/Aureliolo/synthorg/issues/520)) ([181eb8a](https://github.com/Aureliolo/synthorg/commit/181eb8a7f289e72239916a7832fb64bc5b47f1e1)), closes [#416](https://github.com/Aureliolo/synthorg/issues/416)
* implement settings persistence layer (DB-backed config) ([#495](https://github.com/Aureliolo/synthorg/issues/495)) ([4bd99f7](https://github.com/Aureliolo/synthorg/commit/4bd99f7c242cf611ec2dadcaaf3d46a21bec20c3)), closes [#450](https://github.com/Aureliolo/synthorg/issues/450)
* **memory:** implement dual-mode archival in memory consolidation ([#524](https://github.com/Aureliolo/synthorg/issues/524)) ([4603c9e](https://github.com/Aureliolo/synthorg/commit/4603c9e1196b7f5f49e727e3469ce347c7f05f40)), closes [#418](https://github.com/Aureliolo/synthorg/issues/418)
* migrate config consumers to read through SettingsService ([#510](https://github.com/Aureliolo/synthorg/issues/510)) ([32f553d](https://github.com/Aureliolo/synthorg/commit/32f553d6d166d8ba6ca13186de267ae5d9cc5139))
* **settings:** implement settings change subscriptions for service hot-reload ([#526](https://github.com/Aureliolo/synthorg/issues/526)) ([53f908e](https://github.com/Aureliolo/synthorg/commit/53f908ed11a925cbb084f2b8cead8539ca0d138a)), closes [#503](https://github.com/Aureliolo/synthorg/issues/503)
* **settings:** register API config in SettingsService with 2-phase init ([#518](https://github.com/Aureliolo/synthorg/issues/518)) ([29f7481](https://github.com/Aureliolo/synthorg/commit/29f7481c03607592f6c3e3f2051eee4352840637))
* **tools:** add SSRF prevention for git clone URLs ([#505](https://github.com/Aureliolo/synthorg/issues/505)) ([492dd0d](https://github.com/Aureliolo/synthorg/commit/492dd0d40ec7890bb41325214cf9288190aa303d))
* **tools:** wire RootConfig.git_clone to GitCloneTool instantiation ([#519](https://github.com/Aureliolo/synthorg/issues/519)) ([b7d8172](https://github.com/Aureliolo/synthorg/commit/b7d81729e7c39590ed382f71bf005046c94be01f))


### Bug Fixes

* **api:** replace JWT query parameter with one-time ticket for WebSocket auth ([#493](https://github.com/Aureliolo/synthorg/issues/493)) ([22a25f6](https://github.com/Aureliolo/synthorg/commit/22a25f6f03685a3509875ca2d1299c432db34301)), closes [#343](https://github.com/Aureliolo/synthorg/issues/343)


### Documentation

* add uv cache lock contention handling to worktree skill ([#500](https://github.com/Aureliolo/synthorg/issues/500)) ([bd85a8d](https://github.com/Aureliolo/synthorg/commit/bd85a8dd15bb716addb29c96488f22d4d3b21745))
* document RFC 9457 dual response formats in OpenAPI schema ([#506](https://github.com/Aureliolo/synthorg/issues/506)) ([8dd2524](https://github.com/Aureliolo/synthorg/commit/8dd25245ec9c01e0de53f334dec8adb1c5154836))


### Maintenance

* upgrade jsdom from 28 to 29 ([#499](https://github.com/Aureliolo/synthorg/issues/499)) ([1ea2249](https://github.com/Aureliolo/synthorg/commit/1ea2249720482e20281424488a6f17bb012a8410))

## [0.3.0](https://github.com/Aureliolo/synthorg/compare/v0.2.9...v0.3.0) (2026-03-16)


### Features

* **cli:** prettify status output with table, links, and --json flag ([#490](https://github.com/Aureliolo/synthorg/issues/490)) ([61fa8af](https://github.com/Aureliolo/synthorg/commit/61fa8afe55667a34eca3b53aa5604e12f15f36cd))


### Bug Fixes

* **api:** don't require password change after self-service setup ([#488](https://github.com/Aureliolo/synthorg/issues/488)) ([ba13e04](https://github.com/Aureliolo/synthorg/commit/ba13e041969f71ab4dd6925a9487c91dc94776f9))

## [0.2.9](https://github.com/Aureliolo/synthorg/compare/v0.2.8...v0.2.9) (2026-03-16)


### Bug Fixes

* **api:** auto-wire persistence backend from SYNTHORG_DB_PATH env var ([#486](https://github.com/Aureliolo/synthorg/issues/486)) ([7973b07](https://github.com/Aureliolo/synthorg/commit/7973b07f990ee01b369bccabf679101693efc7da))
* **cli:** completion cleanup on uninstall + init backend selection ([#484](https://github.com/Aureliolo/synthorg/issues/484)) ([97ccb51](https://github.com/Aureliolo/synthorg/commit/97ccb51416b55815a9befc8d1b05abedf41ef7d5))
* **docker:** keep sandbox container alive with sleep infinity ([#485](https://github.com/Aureliolo/synthorg/issues/485)) ([b9f400f](https://github.com/Aureliolo/synthorg/commit/b9f400f165a19b37438051029f405f28285c06ba))

## [0.2.8](https://github.com/Aureliolo/synthorg/compare/v0.2.7...v0.2.8) (2026-03-16)


### Features

* add RRF rank fusion to memory ranking ([#478](https://github.com/Aureliolo/synthorg/issues/478)) ([42242b5](https://github.com/Aureliolo/synthorg/commit/42242b51e5f6d4c2d034d569d20652f5175abf91))
* collaboration scoring enhancements -- LLM sampling and human override ([#477](https://github.com/Aureliolo/synthorg/issues/477)) ([b3f3330](https://github.com/Aureliolo/synthorg/commit/b3f33303e9a2dbcb57c59b7ed32cc5fda292398d))


### Bug Fixes

* add .gitattributes to enforce LF line endings for Go files ([#483](https://github.com/Aureliolo/synthorg/issues/483)) ([1b8c7b6](https://github.com/Aureliolo/synthorg/commit/1b8c7b618a55194ddd925b9e0c11a9b481e2f2d7))
* **cli:** Windows uninstall, update UX, health check, sigstore ([#476](https://github.com/Aureliolo/synthorg/issues/476)) ([470ca72](https://github.com/Aureliolo/synthorg/commit/470ca7251235294b7d30a042d28988fea12695c3))


### Refactoring

* **web:** extract WebSocket subscription into reusable composable ([#475](https://github.com/Aureliolo/synthorg/issues/475)) ([96e6c46](https://github.com/Aureliolo/synthorg/commit/96e6c466b115462b1a2248310d5d778e0232592c)), closes [#351](https://github.com/Aureliolo/synthorg/issues/351)


### Maintenance

* bump hypothesis from 6.151.5 to 6.151.9 in the minor-and-patch group ([#482](https://github.com/Aureliolo/synthorg/issues/482)) ([a7297d5](https://github.com/Aureliolo/synthorg/commit/a7297d57401efbca38ae38364c2d39c773cd3ec7))
* bump nginxinc/nginx-unprivileged from `aec540f` to `ccbac1a` in /docker/web ([#479](https://github.com/Aureliolo/synthorg/issues/479)) ([176e052](https://github.com/Aureliolo/synthorg/commit/176e052ec4957652bef98cdbed2d3b864f63f089))

## [0.2.7](https://github.com/Aureliolo/synthorg/compare/v0.2.6...v0.2.7) (2026-03-15)


### Bug Fixes

* pre-create release tag before Release Please runs ([#473](https://github.com/Aureliolo/synthorg/issues/473)) ([568187b](https://github.com/Aureliolo/synthorg/commit/568187b2ff6c30f32ba62910463c42dff102fa57))
* **site:** show success message after contact form submission ([#474](https://github.com/Aureliolo/synthorg/issues/474)) ([f782e48](https://github.com/Aureliolo/synthorg/commit/f782e487241b045e12cb734818e8597b8b3962cd))


### Maintenance

* add path filtering to pre-push Python hooks ([#470](https://github.com/Aureliolo/synthorg/issues/470)) ([ef51797](https://github.com/Aureliolo/synthorg/commit/ef51797d1d8ea1f7e430b7eef94ae8c474248a86))

## [0.2.6](https://github.com/Aureliolo/synthorg/compare/v0.2.5...v0.2.6) (2026-03-15)


### Features

* add intra-loop stagnation detector ([#415](https://github.com/Aureliolo/synthorg/issues/415)) ([#458](https://github.com/Aureliolo/synthorg/issues/458)) ([8e9f34f](https://github.com/Aureliolo/synthorg/commit/8e9f34f9a2118a5cbbbcbc0076e384d353d1b3c2))
* add RFC 9457 structured error responses (Phase 1) ([#457](https://github.com/Aureliolo/synthorg/issues/457)) ([6612a99](https://github.com/Aureliolo/synthorg/commit/6612a994c625489741f2c20887fa9fe91bf232fc)), closes [#419](https://github.com/Aureliolo/synthorg/issues/419)
* implement AgentStateRepository for runtime state persistence ([#459](https://github.com/Aureliolo/synthorg/issues/459)) ([5009da7](https://github.com/Aureliolo/synthorg/commit/5009da7915ef02e79995ecbc3c8425704b9517ba))
* **site:** add SEO essentials, contact form, early-access banner ([#467](https://github.com/Aureliolo/synthorg/issues/467)) ([11b645e](https://github.com/Aureliolo/synthorg/commit/11b645ee8b72aba0b48c17e1c00b3781cedb7b20)), closes [#466](https://github.com/Aureliolo/synthorg/issues/466)


### Bug Fixes

* CLI improvements -- config show, completion install, enhanced doctor, Sigstore verification ([#465](https://github.com/Aureliolo/synthorg/issues/465)) ([9e08cec](https://github.com/Aureliolo/synthorg/commit/9e08cec314faa82d6baf603bf51db31528f41d19))
* **site:** add reCAPTCHA v3, main landmark, and docs sitemap ([#469](https://github.com/Aureliolo/synthorg/issues/469)) ([fa6d35c](https://github.com/Aureliolo/synthorg/commit/fa6d35c0025fd9a7507d1bf3f307f6ce435721c1))
* use force-tag-creation instead of manual tag creation hack ([#462](https://github.com/Aureliolo/synthorg/issues/462)) ([2338004](https://github.com/Aureliolo/synthorg/commit/23380049d5a824731d47a22d7baf64af32375ff2))

## [0.2.5](https://github.com/Aureliolo/synthorg/compare/v0.2.4...v0.2.5) (2026-03-15)


### Features

* default sandbox to enabled, polish CLI output, add sandbox CI build ([#455](https://github.com/Aureliolo/synthorg/issues/455)) ([a4869b6](https://github.com/Aureliolo/synthorg/commit/a4869b698afa252cac951a2903a782d3ab34a5a4))


### Bug Fixes

* export .intoto.jsonl provenance for OpenSSF Scorecard ([#456](https://github.com/Aureliolo/synthorg/issues/456)) ([2feed09](https://github.com/Aureliolo/synthorg/commit/2feed0913244258cd84926da18c4a7d1b5a856f7))


### Maintenance

* add pyrightconfig.json and fix all pyright errors ([#448](https://github.com/Aureliolo/synthorg/issues/448)) ([f60746a](https://github.com/Aureliolo/synthorg/commit/f60746a50f7539b389ff8605cff6b940ecb39856))

## [0.2.4](https://github.com/Aureliolo/synthorg/compare/v0.2.3...v0.2.4) (2026-03-15)


### Bug Fixes

* attach cosign signatures and provenance bundle to release assets ([#438](https://github.com/Aureliolo/synthorg/issues/438)) ([f191a4d](https://github.com/Aureliolo/synthorg/commit/f191a4dc810b90434ee01a44fe9827ac2f738232))
* create git tag explicitly for draft releases ([#432](https://github.com/Aureliolo/synthorg/issues/432)) ([1f5120e](https://github.com/Aureliolo/synthorg/commit/1f5120ee80bca0441a38b399d9b26675e5029df8))
* docker healthcheck, CI optimization, and container hardening ([#436](https://github.com/Aureliolo/synthorg/issues/436)) ([4d32bca](https://github.com/Aureliolo/synthorg/commit/4d32bca76ceb20715457713f16a9d97f284ecb87))
* ensure security headers on all HTTP responses ([#437](https://github.com/Aureliolo/synthorg/issues/437)) ([837f2fc](https://github.com/Aureliolo/synthorg/commit/837f2fcca858cec4e2083a43c5ad5a0899ae6bc9))
* make install scripts usable immediately without terminal restart ([#433](https://github.com/Aureliolo/synthorg/issues/433)) ([b45533c](https://github.com/Aureliolo/synthorg/commit/b45533c9742b6f1d43cf386804d52cf996eaf663))
* migrate pids_limit to deploy.resources.limits.pids ([#439](https://github.com/Aureliolo/synthorg/issues/439)) ([66b94fd](https://github.com/Aureliolo/synthorg/commit/66b94fdb7f1d121df0463a13df01a2f0c3a8f52a))
* use cosign --bundle flag for checksums signing ([#443](https://github.com/Aureliolo/synthorg/issues/443)) ([19735b9](https://github.com/Aureliolo/synthorg/commit/19735b98dbe9152fd1071dd54eb13fa5eee6502c))


### Refactoring

* redesign release notes layout ([#434](https://github.com/Aureliolo/synthorg/issues/434)) ([239aaf7](https://github.com/Aureliolo/synthorg/commit/239aaf783135a9cb163cc573c62975a4fb04ab5a))


### Maintenance

* **main:** release 0.2.4 ([#431](https://github.com/Aureliolo/synthorg/issues/431)) ([63b03c4](https://github.com/Aureliolo/synthorg/commit/63b03c4c1f5c2469aae38f47a89b6e79210ebe4e))
* remove stale v0.2.4 changelog section from failed release ([#446](https://github.com/Aureliolo/synthorg/issues/446)) ([769de10](https://github.com/Aureliolo/synthorg/commit/769de107cf10513469d61e850d9340b5066bf26a))
* reset version to 0.2.3 for re-release ([#444](https://github.com/Aureliolo/synthorg/issues/444)) ([8579993](https://github.com/Aureliolo/synthorg/commit/857999326232b13a41bbe40215ac21460c5b7268))
* **site:** replace hero CTA with license link and scroll arrow ([#440](https://github.com/Aureliolo/synthorg/issues/440)) ([56af41c](https://github.com/Aureliolo/synthorg/commit/56af41c833d82857ccf3f60020687ed7d85ca49d))
* **web:** adopt @vue/tsconfig preset ([#435](https://github.com/Aureliolo/synthorg/issues/435)) ([7d4b214](https://github.com/Aureliolo/synthorg/commit/7d4b214cc22ec99b922dd46bb529e9c8af476b23))

## [0.2.3](https://github.com/Aureliolo/synthorg/compare/v0.2.2...v0.2.3) (2026-03-15)


### Bug Fixes

* use draft releases to support immutable release policy ([#429](https://github.com/Aureliolo/synthorg/issues/429)) ([a6c7444](https://github.com/Aureliolo/synthorg/commit/a6c7444b12239422a98a81c294a139cce96abc4a))

## [0.2.2](https://github.com/Aureliolo/synthorg/compare/v0.2.1...v0.2.2) (2026-03-15)


### Bug Fixes

* restore golangci-lint to v2.11.3 (broken by replace_all in [#425](https://github.com/Aureliolo/synthorg/issues/425)) ([#427](https://github.com/Aureliolo/synthorg/issues/427)) ([8ba9375](https://github.com/Aureliolo/synthorg/commit/8ba9375fbb4f8372486a05f3a6600f2a5710702d))

## [0.2.1](https://github.com/Aureliolo/synthorg/compare/v0.2.0...v0.2.1) (2026-03-15)


### Bug Fixes

* upgrade goreleaser to v2.14.3 (v2.11.3 never existed) ([#425](https://github.com/Aureliolo/synthorg/issues/425)) ([df6650d](https://github.com/Aureliolo/synthorg/commit/df6650d514976d6dfd73babe94fda107dd5714f1))

## [0.2.0](https://github.com/Aureliolo/synthorg/compare/v0.1.4...v0.2.0) (2026-03-15)


### Features

* add /get/ installation page for CLI installer ([#413](https://github.com/Aureliolo/synthorg/issues/413)) ([6a47e4a](https://github.com/Aureliolo/synthorg/commit/6a47e4aa89db39ebde26b33b4bc7bde713262e6a))
* add cross-platform Go CLI for container lifecycle management ([#401](https://github.com/Aureliolo/synthorg/issues/401)) ([0353d9e](https://github.com/Aureliolo/synthorg/commit/0353d9ee46a0a97d425ae8daba087a6b72228a43)), closes [#392](https://github.com/Aureliolo/synthorg/issues/392)
* add explicit ScanOutcome signal to OutputScanResult ([#394](https://github.com/Aureliolo/synthorg/issues/394)) ([be33414](https://github.com/Aureliolo/synthorg/commit/be334141af54dbe5bc16ac2380350f76da012c38)), closes [#284](https://github.com/Aureliolo/synthorg/issues/284)
* add meeting scheduler, event-triggered meetings, and Go CLI lint fixes ([#407](https://github.com/Aureliolo/synthorg/issues/407)) ([5550fa1](https://github.com/Aureliolo/synthorg/commit/5550fa1cf31f86c39183bf002fba793e19392cb6))
* wire MultiAgentCoordinator into runtime ([#396](https://github.com/Aureliolo/synthorg/issues/396)) ([7a9e516](https://github.com/Aureliolo/synthorg/commit/7a9e5166952693a23d22f2bfb6750557c89936db))


### Bug Fixes

* CLA signatures branch + declutter repo root ([#409](https://github.com/Aureliolo/synthorg/issues/409)) ([cabe953](https://github.com/Aureliolo/synthorg/commit/cabe95301d4e44267aa2ddfd93580083b7e8c155))
* correct Release Please branch name in release workflow ([#410](https://github.com/Aureliolo/synthorg/issues/410)) ([515d816](https://github.com/Aureliolo/synthorg/commit/515d816b36fba0d7b95385df92f3f7e711936d40))
* replace slsa-github-generator with attest-build-provenance, fix DAST ([#424](https://github.com/Aureliolo/synthorg/issues/424)) ([eeaadff](https://github.com/Aureliolo/synthorg/commit/eeaadff789f6179fe99874cf9f2caf89b6d3e6ea))
* resolve CodeQL path-injection alerts in Go CLI ([#412](https://github.com/Aureliolo/synthorg/issues/412)) ([f41bf16](https://github.com/Aureliolo/synthorg/commit/f41bf1642fbf6c5242d336980bb7e413b52d2c13))


### Refactoring

* rename package from ai_company to synthorg ([#422](https://github.com/Aureliolo/synthorg/issues/422)) ([df27c6e](https://github.com/Aureliolo/synthorg/commit/df27c6e4c43546201e9e9d459981039acf177655)), closes [#398](https://github.com/Aureliolo/synthorg/issues/398)


### Tests

* add fuzz and property-based testing across all layers ([#421](https://github.com/Aureliolo/synthorg/issues/421)) ([115a742](https://github.com/Aureliolo/synthorg/commit/115a742c7259a08c9c714ac47ec52704fb997e49))


### CI/CD

* add SLSA L3 provenance for CLI binaries and container images ([#423](https://github.com/Aureliolo/synthorg/issues/423)) ([d3dc75d](https://github.com/Aureliolo/synthorg/commit/d3dc75d3f6449959504ddd032d56e769f5f3f679))
* bump the major group with 4 updates ([#405](https://github.com/Aureliolo/synthorg/issues/405)) ([20c7a04](https://github.com/Aureliolo/synthorg/commit/20c7a04a5fccd3296cdad57df4af757e9f6abb57))


### Maintenance

* bump github.com/spf13/cobra from 1.9.1 to 1.10.2 in /cli in the minor-and-patch group ([#402](https://github.com/Aureliolo/synthorg/issues/402)) ([e31edbb](https://github.com/Aureliolo/synthorg/commit/e31edbb5d2e21439c88c98ef8c1847d23759e94a))
* narrow BSL Additional Use Grant and add CLA ([#408](https://github.com/Aureliolo/synthorg/issues/408)) ([5ab15bd](https://github.com/Aureliolo/synthorg/commit/5ab15bd1355063144076d735815ad3e34d1cbbb0)), closes [#406](https://github.com/Aureliolo/synthorg/issues/406)

## [0.1.4](https://github.com/Aureliolo/synthorg/compare/v0.1.3...v0.1.4) (2026-03-14)


### License

* narrow BSL 1.1 Additional Use Grant -- free production use for non-competing organizations with fewer than 500 employees and contractors ([#406](https://github.com/Aureliolo/synthorg/issues/406))
* add Contributor License Agreement (CLA) with automated enforcement for dual-licensing support
* add licensing rationale documentation page explaining BSL choice, what's permitted, and invitation for feedback
* auto-update BSL Change Date to 3 years ahead on each release


### Features

* add approval workflow gates to TaskEngine ([#387](https://github.com/Aureliolo/synthorg/issues/387)) ([2db968a](https://github.com/Aureliolo/synthorg/commit/2db968a21fb0f0afa42eb990ee70cbb7b71d2ae5))
* implement checkpoint recovery strategy ([#367](https://github.com/Aureliolo/synthorg/issues/367)) ([f886838](https://github.com/Aureliolo/synthorg/commit/f886838af9789a1ed9834f1c16883e893c764761))


### CI/CD

* add npm and pre-commit ecosystems to Dependabot ([#369](https://github.com/Aureliolo/synthorg/issues/369)) ([54e5fe7](https://github.com/Aureliolo/synthorg/commit/54e5fe7c0351cd44ffd638fe662a4394e4a2eeea))
* bump actions/setup-node from 4.4.0 to 6.3.0 ([#360](https://github.com/Aureliolo/synthorg/issues/360)) ([2db5105](https://github.com/Aureliolo/synthorg/commit/2db5105429da3937daa8d0bb58593cc8dacd756f))
* bump github/codeql-action from 3.32.6 to 4.32.6 ([#361](https://github.com/Aureliolo/synthorg/issues/361)) ([ce766e8](https://github.com/Aureliolo/synthorg/commit/ce766e8b655af0cb80674a37eabd379ba0794011))
* group major dependabot bumps per ecosystem ([#388](https://github.com/Aureliolo/synthorg/issues/388)) ([3c43aef](https://github.com/Aureliolo/synthorg/commit/3c43aef7660a78fbfa337d448d954d21ed928806))


### Maintenance

* bump @vitejs/plugin-vue from 5.2.4 to 6.0.5 in /web ([#382](https://github.com/Aureliolo/synthorg/issues/382)) ([d7054ee](https://github.com/Aureliolo/synthorg/commit/d7054ee2c379341211e8dbd088ec2d03f8887e9c))
* bump @vue/tsconfig from 0.7.0 to 0.9.0 in /web in the minor-and-patch group across 1 directory ([#371](https://github.com/Aureliolo/synthorg/issues/371)) ([64fa08b](https://github.com/Aureliolo/synthorg/commit/64fa08b63ab8e7c30fbb2b94c40b2a3aa9044df4))
* bump astro from 5.18.1 to 6.0.4 in /site ([#376](https://github.com/Aureliolo/synthorg/issues/376)) ([d349317](https://github.com/Aureliolo/synthorg/commit/d349317a189e97e625b39d038387b9e324e56d8f))
* bump https://github.com/astral-sh/ruff-pre-commit from v0.15.5 to 0.15.6 ([#372](https://github.com/Aureliolo/synthorg/issues/372)) ([dcacb2e](https://github.com/Aureliolo/synthorg/commit/dcacb2eb3a890b7b1e4907a881b3fe87ba11f083))
* bump https://github.com/gitleaks/gitleaks from v8.24.3 to 8.30.1 ([#375](https://github.com/Aureliolo/synthorg/issues/375)) ([a18e6ed](https://github.com/Aureliolo/synthorg/commit/a18e6ed04f9bcb02b132f1e0ff161046948bc3fa))
* bump https://github.com/hadolint/hadolint from v2.12.0 to 2.14.0 ([#373](https://github.com/Aureliolo/synthorg/issues/373)) ([47b906b](https://github.com/Aureliolo/synthorg/commit/47b906b63e46678d2acec50f5a45f2916cd65086))
* bump https://github.com/pre-commit/pre-commit-hooks from v5.0.0 to 6.0.0 ([#374](https://github.com/Aureliolo/synthorg/issues/374)) ([1926555](https://github.com/Aureliolo/synthorg/commit/1926555d8116d3d60902bce789c1f3e0e6ec96e0))
* bump litellm from 1.82.1 to 1.82.2 in the minor-and-patch group ([#385](https://github.com/Aureliolo/synthorg/issues/385)) ([fa4f7b7](https://github.com/Aureliolo/synthorg/commit/fa4f7b76809c7b8c2c99c839b23994f87c7a60b5))
* bump node from 22-alpine to 25-alpine in /docker/web ([#359](https://github.com/Aureliolo/synthorg/issues/359)) ([8d56cd3](https://github.com/Aureliolo/synthorg/commit/8d56cd362ff1ce544d2be858751e504cace1b12c))
* bump node from 22-slim to 25-slim in /docker/sandbox ([#358](https://github.com/Aureliolo/synthorg/issues/358)) ([3de8748](https://github.com/Aureliolo/synthorg/commit/3de8748af5a77058f5d6f496a693b731f13b1490))
* bump pinia from 2.3.1 to 3.0.4 in /web ([#381](https://github.com/Aureliolo/synthorg/issues/381)) ([c78dcc2](https://github.com/Aureliolo/synthorg/commit/c78dcc2de57340836d504b836242c2e67497a7db))
* bump the major group across 1 directory with 9 updates ([#389](https://github.com/Aureliolo/synthorg/issues/389)) ([9fa621b](https://github.com/Aureliolo/synthorg/commit/9fa621bc33d78373608179b639108cb03dc83115))
* bump the minor-and-patch group with 2 updates ([#362](https://github.com/Aureliolo/synthorg/issues/362)) ([6ede2ce](https://github.com/Aureliolo/synthorg/commit/6ede2cee6fe97b39b37e37997b7cc102fd0f5994))
* bump vue-router from 4.6.4 to 5.0.3 in /web ([#378](https://github.com/Aureliolo/synthorg/issues/378)) ([6c60f6c](https://github.com/Aureliolo/synthorg/commit/6c60f6c822d7a975c012dc588041348f119e0744))
* expand review skills to 18 smart conditional agents ([#364](https://github.com/Aureliolo/synthorg/issues/364)) ([494013f](https://github.com/Aureliolo/synthorg/commit/494013feb1c6d4e7977c75cf4f42714bf1615bbd))

## [0.1.3](https://github.com/Aureliolo/synthorg/compare/v0.1.2...v0.1.3) (2026-03-13)


### Features

* add Mem0 memory backend adapter ([#345](https://github.com/Aureliolo/synthorg/issues/345)) ([2788db8](https://github.com/Aureliolo/synthorg/commit/2788db881f85a4e9e211b834b396943b3c588edf)), closes [#206](https://github.com/Aureliolo/synthorg/issues/206)
* centralized single-writer TaskEngine with full CRUD API ([#328](https://github.com/Aureliolo/synthorg/issues/328)) ([9c1a3e1](https://github.com/Aureliolo/synthorg/commit/9c1a3e150082ec4263148b4fcc71e5ba7c7a072d))
* incremental AgentEngine → TaskEngine status sync ([#331](https://github.com/Aureliolo/synthorg/issues/331)) ([7a68d34](https://github.com/Aureliolo/synthorg/commit/7a68d34a815efc20dbe409a597a93b9820b2227b)), closes [#323](https://github.com/Aureliolo/synthorg/issues/323)
* web dashboard pages -- views, components, tests, and review fixes ([#354](https://github.com/Aureliolo/synthorg/issues/354)) ([b165ec4](https://github.com/Aureliolo/synthorg/commit/b165ec4d5d3e2a70852ef952a417fbcb053129c2))
* web dashboard with Vue 3 + PrimeVue + Tailwind CSS ([#347](https://github.com/Aureliolo/synthorg/issues/347)) ([06416b1](https://github.com/Aureliolo/synthorg/commit/06416b1d876528754db5a1363d5ebb58bde1bf2a))


### Bug Fixes

* harden coordination pipeline with validators, logging, and fail-fast ([#333](https://github.com/Aureliolo/synthorg/issues/333)) ([2f10d49](https://github.com/Aureliolo/synthorg/commit/2f10d495df099ba7d8eaaceb0cd717670fad7748)), closes [#205](https://github.com/Aureliolo/synthorg/issues/205)
* repo-wide security hardening from ZAP, Scorecard, and CodeQL audit ([#357](https://github.com/Aureliolo/synthorg/issues/357)) ([27eb288](https://github.com/Aureliolo/synthorg/commit/27eb28840ecffaa34a030e99d19add3af00a38f0))


### CI/CD

* add pip-audit, hadolint, OSSF Scorecard, ZAP DAST, and pre-push hooks ([#350](https://github.com/Aureliolo/synthorg/issues/350)) ([2802d20](https://github.com/Aureliolo/synthorg/commit/2802d20d16897582021b516fb3742495ff3fc30e))
* add workflow_dispatch trigger to PR Preview for Dependabot PRs ([#326](https://github.com/Aureliolo/synthorg/issues/326)) ([4c7b6d9](https://github.com/Aureliolo/synthorg/commit/4c7b6d935ef842ced3269ecad0a9e7b011adbf3b))
* bump astral-sh/setup-uv from 7.4.0 to 7.5.0 in the minor-and-patch group ([#335](https://github.com/Aureliolo/synthorg/issues/335)) ([98dd8ca](https://github.com/Aureliolo/synthorg/commit/98dd8caa30f60ad99e6cf46fa7d116db5e61a5b6))


### Maintenance

* bump the minor-and-patch group across 1 directory with 3 updates ([#352](https://github.com/Aureliolo/synthorg/issues/352)) ([031b1c9](https://github.com/Aureliolo/synthorg/commit/031b1c95fc069d38134e070d92ae121164e18233))
* **deps:** bump devalue from 5.6.3 to 5.6.4 in /site in the npm_and_yarn group across 1 directory ([#324](https://github.com/Aureliolo/synthorg/issues/324)) ([9a9c600](https://github.com/Aureliolo/synthorg/commit/9a9c600509d2d77c3487082521a8c3496cd3d6c1))
* migrate docs build from MkDocs to Zensical ([#330](https://github.com/Aureliolo/synthorg/issues/330)) ([fa8bf1d](https://github.com/Aureliolo/synthorg/commit/fa8bf1dfcc129e334d1b426dd5f9560aec0a6e81)), closes [#329](https://github.com/Aureliolo/synthorg/issues/329)

## [0.1.2](https://github.com/Aureliolo/synthorg/compare/v0.1.1...v0.1.2) (2026-03-12)


### Features

* add /review-dep-pr skill for dependency update PR review ([#315](https://github.com/Aureliolo/synthorg/issues/315)) ([56f6565](https://github.com/Aureliolo/synthorg/commit/56f6565d801ff47f2a86f09dfc3f2693472dc62b))
* add static OpenAPI reference page with Scalar UI ([#319](https://github.com/Aureliolo/synthorg/issues/319)) ([77cdbcc](https://github.com/Aureliolo/synthorg/commit/77cdbccb71fcb974665350b892b98fd8af43e635))


### Bug Fixes

* correct API reference link path in rest-api.md ([#320](https://github.com/Aureliolo/synthorg/issues/320)) ([3d08f92](https://github.com/Aureliolo/synthorg/commit/3d08f92278ba082b7ed44d8306b49151d8b0ebe2))


### CI/CD

* bump actions/setup-node from 4.4.0 to 6.3.0 ([#311](https://github.com/Aureliolo/synthorg/issues/311)) ([3c99d6f](https://github.com/Aureliolo/synthorg/commit/3c99d6fd9711d526e07304cf756968796026f821))
* bump actions/setup-python from 5.6.0 to 6.2.0 ([#312](https://github.com/Aureliolo/synthorg/issues/312)) ([3273553](https://github.com/Aureliolo/synthorg/commit/327355336046db5fa67bdca88fa6f3fba059aac0))
* bump astral-sh/setup-uv from 6.0.1 to 7.4.0 ([#310](https://github.com/Aureliolo/synthorg/issues/310)) ([b63cee7](https://github.com/Aureliolo/synthorg/commit/b63cee7fc5550da799161943b5ac25894aeeda7e))


### Maintenance

* bump mkdocstrings[python] from 0.29.1 to 1.0.3 ([#314](https://github.com/Aureliolo/synthorg/issues/314)) ([d46ccad](https://github.com/Aureliolo/synthorg/commit/d46ccadda5b8db00b2f7e848a6e678a56f6972ff))
* bump the minor-and-patch group with 2 updates ([#313](https://github.com/Aureliolo/synthorg/issues/313)) ([6337ae4](https://github.com/Aureliolo/synthorg/commit/6337ae4b90e52d869db8ba253dce59c2c3387ca1))
* improve review-dep-pr skill and add Codecov Test Analytics ([#317](https://github.com/Aureliolo/synthorg/issues/317)) ([eb5782e](https://github.com/Aureliolo/synthorg/commit/eb5782ee31749a3926a0262bc8bce4e8cd303413))

## [0.1.1](https://github.com/Aureliolo/synthorg/compare/v0.1.0...v0.1.1) (2026-03-11)


### Features

* add PR preview deployments via Cloudflare Pages ([#302](https://github.com/Aureliolo/synthorg/issues/302)) ([b73c45a](https://github.com/Aureliolo/synthorg/commit/b73c45a806d3edc5f541263485cf9e922748ea17))


### Bug Fixes

* correct deploy-pages SHA and improve preview cleanup reliability ([#304](https://github.com/Aureliolo/synthorg/issues/304)) ([584d64a](https://github.com/Aureliolo/synthorg/commit/584d64a1f564a7ad75ad3ffaa9a44eabf788a707))
* harden API key hashing with HMAC-SHA256 and clean up legacy changelog ([#292](https://github.com/Aureliolo/synthorg/issues/292)) ([5e85353](https://github.com/Aureliolo/synthorg/commit/5e85353c29b1748fb16034f2d9f92166fb5c0908))
* upgrade upload-pages-artifact to v4 and add zizmor workflow linting ([#299](https://github.com/Aureliolo/synthorg/issues/299)) ([2eac571](https://github.com/Aureliolo/synthorg/commit/2eac571bc44273ff99a2e10448859561b90328f3))
* use Cloudflare Pages API default per_page for pagination ([#305](https://github.com/Aureliolo/synthorg/issues/305)) ([9fec245](https://github.com/Aureliolo/synthorg/commit/9fec245e202ce58df4327ffd2055e501e53551a3))


### Documentation

* remove milestone references and rebrand to SynthOrg ([#289](https://github.com/Aureliolo/synthorg/issues/289)) ([57a03e0](https://github.com/Aureliolo/synthorg/commit/57a03e0193f17ca1b9f0064841201ded33eb70ea))
* set up documentation site, release CI, and sandbox hardening ([#298](https://github.com/Aureliolo/synthorg/issues/298)) ([0dec9da](https://github.com/Aureliolo/synthorg/commit/0dec9da5ce88aa212a1b74d99340497f3d4bd843))
* split DESIGN_SPEC.md into 7 focused design pages ([#308](https://github.com/Aureliolo/synthorg/issues/308)) ([9ea0788](https://github.com/Aureliolo/synthorg/commit/9ea078818cace13729adf27647a75d800571069c))

## [0.1.0](https://github.com/Aureliolo/synthorg/compare/v0.0.0...v0.1.0) (2026-03-11)


### Features

* add autonomy levels and approval timeout policies ([#42](https://github.com/Aureliolo/synthorg/issues/42), [#126](https://github.com/Aureliolo/synthorg/issues/126)) ([#197](https://github.com/Aureliolo/synthorg/issues/197)) ([eecc25a](https://github.com/Aureliolo/synthorg/commit/eecc25a1177f15101d02fb3dc7b95f3d9c023279))
* add CFO cost optimization service with anomaly detection, reports, and approval decisions ([#186](https://github.com/Aureliolo/synthorg/issues/186)) ([a7fa00b](https://github.com/Aureliolo/synthorg/commit/a7fa00bf9ef113b02aa8ef4bc13ddcb8c61ea972))
* add code quality toolchain (ruff, mypy, pre-commit, dependabot) ([#63](https://github.com/Aureliolo/synthorg/issues/63)) ([36681a8](https://github.com/Aureliolo/synthorg/commit/36681a8c44d31a2c6e9acc3f55eea7d108c3c36c))
* add configurable cost tiers and subscription/quota-aware tracking ([#67](https://github.com/Aureliolo/synthorg/issues/67)) ([#185](https://github.com/Aureliolo/synthorg/issues/185)) ([9baedfa](https://github.com/Aureliolo/synthorg/commit/9baedfa5c134c9803065b5c7cd524ff03c66ce4f))
* add container packaging, Docker Compose, and CI pipeline ([#269](https://github.com/Aureliolo/synthorg/issues/269)) ([435bdfe](https://github.com/Aureliolo/synthorg/commit/435bdfed1e7a5df5767ff31d991021bf3dfd3e12)), closes [#267](https://github.com/Aureliolo/synthorg/issues/267)
* add coordination error taxonomy classification pipeline ([#146](https://github.com/Aureliolo/synthorg/issues/146)) ([#181](https://github.com/Aureliolo/synthorg/issues/181)) ([70c7480](https://github.com/Aureliolo/synthorg/commit/70c748010325824f44f77a798e48241f4703ee0a))
* add cost-optimized, hierarchical, and auction assignment strategies ([#175](https://github.com/Aureliolo/synthorg/issues/175)) ([ce924fa](https://github.com/Aureliolo/synthorg/commit/ce924faba2fdb10ab430c35f530a750cfd709b30)), closes [#173](https://github.com/Aureliolo/synthorg/issues/173)
* add design specification, license, and project setup ([8669a09](https://github.com/Aureliolo/synthorg/commit/8669a0947d92647bc6a7d7be2a5b334710e5808a))
* add env var substitution and config file auto-discovery ([#77](https://github.com/Aureliolo/synthorg/issues/77)) ([7f53832](https://github.com/Aureliolo/synthorg/commit/7f53832f9c62210658e91a0e2cf980332deea603))
* add FastestStrategy routing + vendor-agnostic cleanup ([#140](https://github.com/Aureliolo/synthorg/issues/140)) ([09619cb](https://github.com/Aureliolo/synthorg/commit/09619cb7dc8f7e6bacd5ec4b6beb1b0ca8475149)), closes [#139](https://github.com/Aureliolo/synthorg/issues/139)
* add HR engine and performance tracking ([#45](https://github.com/Aureliolo/synthorg/issues/45), [#47](https://github.com/Aureliolo/synthorg/issues/47)) ([#193](https://github.com/Aureliolo/synthorg/issues/193)) ([2d091ea](https://github.com/Aureliolo/synthorg/commit/2d091eaef9219ff68520b65e6427bcf2ec025fc5))
* add issue auto-search and resolution verification to PR review skill ([#119](https://github.com/Aureliolo/synthorg/issues/119)) ([deecc39](https://github.com/Aureliolo/synthorg/commit/deecc394c7a90ffc1c69f31eb5c170ebd0cf3250))
* add mandatory JWT + API key authentication ([#256](https://github.com/Aureliolo/synthorg/issues/256)) ([c279cfe](https://github.com/Aureliolo/synthorg/commit/c279cfe9527ee74d97a97634c5beb193f3331320))
* add memory retrieval, ranking, and context injection pipeline ([#41](https://github.com/Aureliolo/synthorg/issues/41)) ([873b0aa](https://github.com/Aureliolo/synthorg/commit/873b0aaf838ff06e2c2c1bf9785c83447228d81e))
* add pluggable MemoryBackend protocol with models, config, and events ([#180](https://github.com/Aureliolo/synthorg/issues/180)) ([46cfdd4](https://github.com/Aureliolo/synthorg/commit/46cfdd423aadf2f5f22b3f13c98313855bfbc26f))
* add pluggable MemoryBackend protocol with models, config, and events ([#32](https://github.com/Aureliolo/synthorg/issues/32)) ([46cfdd4](https://github.com/Aureliolo/synthorg/commit/46cfdd423aadf2f5f22b3f13c98313855bfbc26f))
* add pluggable output scan response policies ([#263](https://github.com/Aureliolo/synthorg/issues/263)) ([b9907e8](https://github.com/Aureliolo/synthorg/commit/b9907e8d77546b4a7a4bb1a31094975fc583be7d))
* add pluggable PersistenceBackend protocol with SQLite implementation ([#36](https://github.com/Aureliolo/synthorg/issues/36)) ([f753779](https://github.com/Aureliolo/synthorg/commit/f753779bd5628d12ade34d4250db7a768de9a975))
* add progressive trust and promotion/demotion subsystems ([#43](https://github.com/Aureliolo/synthorg/issues/43), [#49](https://github.com/Aureliolo/synthorg/issues/49)) ([3a87c08](https://github.com/Aureliolo/synthorg/commit/3a87c0836ea95290eafa42ce4cfec4564c1cd36a))
* add retry handler, rate limiter, and provider resilience ([#100](https://github.com/Aureliolo/synthorg/issues/100)) ([b890545](https://github.com/Aureliolo/synthorg/commit/b8905453fa51a2ca60ffa05f6c4d3598e1d11bc7))
* add SecOps security agent with rule engine, audit log, and ToolInvoker integration ([#40](https://github.com/Aureliolo/synthorg/issues/40)) ([83b7b6c](https://github.com/Aureliolo/synthorg/commit/83b7b6cd062f16353b19ad0ab8ad41b2d951ac16))
* add shared org memory and memory consolidation/archival ([#125](https://github.com/Aureliolo/synthorg/issues/125), [#48](https://github.com/Aureliolo/synthorg/issues/48)) ([4a0832b](https://github.com/Aureliolo/synthorg/commit/4a0832b10194232a133c61b6dd6fb12fc579f951))
* design unified provider interface ([#86](https://github.com/Aureliolo/synthorg/issues/86)) ([3e23d64](https://github.com/Aureliolo/synthorg/commit/3e23d6422b2bd76979ad01af65876bc95928bdcc))
* expand template presets, rosters, and add inheritance ([#80](https://github.com/Aureliolo/synthorg/issues/80), [#81](https://github.com/Aureliolo/synthorg/issues/81), [#84](https://github.com/Aureliolo/synthorg/issues/84)) ([15a9134](https://github.com/Aureliolo/synthorg/commit/15a91349d7e0305d0d33c9de8eb283fdd2184442))
* implement agent runtime state vs immutable config split ([#115](https://github.com/Aureliolo/synthorg/issues/115)) ([4cb1ca5](https://github.com/Aureliolo/synthorg/commit/4cb1ca541ccfa5bea44e4b197eedc24e79179c21))
* implement AgentEngine core orchestrator ([#11](https://github.com/Aureliolo/synthorg/issues/11)) ([#143](https://github.com/Aureliolo/synthorg/issues/143)) ([f2eb73a](https://github.com/Aureliolo/synthorg/commit/f2eb73a1c1864c844b547caf71890354f6031a69))
* implement AuditRepository for security audit log persistence ([#279](https://github.com/Aureliolo/synthorg/issues/279)) ([94bc29f](https://github.com/Aureliolo/synthorg/commit/94bc29fbf745576da51b8b942faf7aa0047dbe9a))
* implement basic tool system (registry, invocation, results) ([#15](https://github.com/Aureliolo/synthorg/issues/15)) ([c51068b](https://github.com/Aureliolo/synthorg/commit/c51068b11de77fb15699c203840651044ab482fa))
* implement built-in file system tools ([#18](https://github.com/Aureliolo/synthorg/issues/18)) ([325ef98](https://github.com/Aureliolo/synthorg/commit/325ef988c2c5312215c7eaf20401d904863c049d))
* implement communication foundation -- message bus, dispatcher, and messenger ([#157](https://github.com/Aureliolo/synthorg/issues/157)) ([8e71bfd](https://github.com/Aureliolo/synthorg/commit/8e71bfd0e3cf84dd36c48f17b933d0554c6f932e))
* implement company template system with 7 built-in presets ([#85](https://github.com/Aureliolo/synthorg/issues/85)) ([cbf1496](https://github.com/Aureliolo/synthorg/commit/cbf14963be4547749d493e1ba5cc40d75c67a6c5))
* implement conflict resolution protocol ([#122](https://github.com/Aureliolo/synthorg/issues/122)) ([#166](https://github.com/Aureliolo/synthorg/issues/166)) ([e03f9f2](https://github.com/Aureliolo/synthorg/commit/e03f9f2e09c0493d5ca51a98d83481bd828b9113))
* implement core entity and role system models ([#69](https://github.com/Aureliolo/synthorg/issues/69)) ([acf9801](https://github.com/Aureliolo/synthorg/commit/acf9801f4b68b1538c07329d9d61771267978bce))
* implement crash recovery with fail-and-reassign strategy ([#149](https://github.com/Aureliolo/synthorg/issues/149)) ([e6e91ed](https://github.com/Aureliolo/synthorg/commit/e6e91ed3dd19397c3d9d456bbdd8cc2fd8c1cfac))
* implement engine extensions -- Plan-and-Execute loop and call categorization ([#134](https://github.com/Aureliolo/synthorg/issues/134), [#135](https://github.com/Aureliolo/synthorg/issues/135)) ([#159](https://github.com/Aureliolo/synthorg/issues/159)) ([9b2699f](https://github.com/Aureliolo/synthorg/commit/9b2699f3b9b1b07912a6a09e0cd21644f432d744))
* implement enterprise logging system with structlog ([#73](https://github.com/Aureliolo/synthorg/issues/73)) ([2f787e5](https://github.com/Aureliolo/synthorg/commit/2f787e5b2576a0403f6b86c9daa16dfbbfd2e243))
* implement graceful shutdown with cooperative timeout strategy ([#130](https://github.com/Aureliolo/synthorg/issues/130)) ([6592515](https://github.com/Aureliolo/synthorg/commit/6592515617742851c1d355422ac40266af3b5127))
* implement hierarchical delegation and loop prevention ([#12](https://github.com/Aureliolo/synthorg/issues/12), [#17](https://github.com/Aureliolo/synthorg/issues/17)) ([6be60b6](https://github.com/Aureliolo/synthorg/commit/6be60b65dd6cac4f61a023b274353325e1690eae))
* implement LiteLLM driver and provider registry ([#88](https://github.com/Aureliolo/synthorg/issues/88)) ([ae3f18b](https://github.com/Aureliolo/synthorg/commit/ae3f18b22ca81e99fea84c9f0ccbab8da1ee5605)), closes [#4](https://github.com/Aureliolo/synthorg/issues/4)
* implement LLM decomposition strategy and workspace isolation ([#174](https://github.com/Aureliolo/synthorg/issues/174)) ([aa0eefe](https://github.com/Aureliolo/synthorg/commit/aa0eefe2a1ef3d945adea10979fd4eea45c8c1d7))
* implement meeting protocol system ([#123](https://github.com/Aureliolo/synthorg/issues/123)) ([ee7caca](https://github.com/Aureliolo/synthorg/commit/ee7cacacad859427c7a2a67f4ce5e72046b15b1b))
* implement message and communication domain models ([#74](https://github.com/Aureliolo/synthorg/issues/74)) ([560a5d2](https://github.com/Aureliolo/synthorg/commit/560a5d2e29625aeae080babeed1ddb4195dc3743))
* implement model routing engine ([#99](https://github.com/Aureliolo/synthorg/issues/99)) ([d3c250b](https://github.com/Aureliolo/synthorg/commit/d3c250b8f341fcf0fece7373c1b295e05f83721c))
* implement parallel agent execution ([#22](https://github.com/Aureliolo/synthorg/issues/22)) ([#161](https://github.com/Aureliolo/synthorg/issues/161)) ([65940b3](https://github.com/Aureliolo/synthorg/commit/65940b3f5bb10692d257fbfda6f4bc692db8aab4))
* implement per-call cost tracking service ([#7](https://github.com/Aureliolo/synthorg/issues/7)) ([#102](https://github.com/Aureliolo/synthorg/issues/102)) ([c4f1f1c](https://github.com/Aureliolo/synthorg/commit/c4f1f1c9952991fbccc3a44dd4c4b2e65cdd9033))
* implement personality injection and system prompt construction ([#105](https://github.com/Aureliolo/synthorg/issues/105)) ([934dd85](https://github.com/Aureliolo/synthorg/commit/934dd85c499a922496392865cf35edb1e75166bd))
* implement single-task execution lifecycle ([#21](https://github.com/Aureliolo/synthorg/issues/21)) ([#144](https://github.com/Aureliolo/synthorg/issues/144)) ([c7e64e4](https://github.com/Aureliolo/synthorg/commit/c7e64e46f85dbd8d2b8b01aad7babd3a00f78bdb))
* implement subprocess sandbox for tool execution isolation ([#131](https://github.com/Aureliolo/synthorg/issues/131)) ([#153](https://github.com/Aureliolo/synthorg/issues/153)) ([3c8394e](https://github.com/Aureliolo/synthorg/commit/3c8394e905b914de1c81b5d7ed1544920cfb1411))
* implement task assignment subsystem with pluggable strategies ([#172](https://github.com/Aureliolo/synthorg/issues/172)) ([c7f1b26](https://github.com/Aureliolo/synthorg/commit/c7f1b2628e37821f01605a206d5f36d5ec6f6c95)), closes [#26](https://github.com/Aureliolo/synthorg/issues/26) [#30](https://github.com/Aureliolo/synthorg/issues/30)
* implement task decomposition and routing engine ([#14](https://github.com/Aureliolo/synthorg/issues/14)) ([9c7fb52](https://github.com/Aureliolo/synthorg/commit/9c7fb526e7a469b8fd4a1ee106670b292a24879a))
* implement Task, Project, Artifact, Budget, and Cost domain models ([#71](https://github.com/Aureliolo/synthorg/issues/71)) ([81eabf1](https://github.com/Aureliolo/synthorg/commit/81eabf1042d30ab67a6e1c0976d0da58b78a9ab9))
* implement tool permission checking ([#16](https://github.com/Aureliolo/synthorg/issues/16)) ([833c190](https://github.com/Aureliolo/synthorg/commit/833c190de2d886ca5cb516341d50e4fb86bc6879))
* implement YAML config loader with Pydantic validation ([#59](https://github.com/Aureliolo/synthorg/issues/59)) ([ff3a2ba](https://github.com/Aureliolo/synthorg/commit/ff3a2ba973f915d8d7f71311188d71d1e461285d))
* implement YAML config loader with Pydantic validation ([#75](https://github.com/Aureliolo/synthorg/issues/75)) ([ff3a2ba](https://github.com/Aureliolo/synthorg/commit/ff3a2ba973f915d8d7f71311188d71d1e461285d))
* initialize project with uv, hatchling, and src layout ([39005f9](https://github.com/Aureliolo/synthorg/commit/39005f96bc665123fa25ce55121ae8fe25bc8cc3))
* initialize project with uv, hatchling, and src layout ([#62](https://github.com/Aureliolo/synthorg/issues/62)) ([39005f9](https://github.com/Aureliolo/synthorg/commit/39005f96bc665123fa25ce55121ae8fe25bc8cc3))
* Litestar REST API, WebSocket feed, and approval queue (M6) ([#189](https://github.com/Aureliolo/synthorg/issues/189)) ([29fcd08](https://github.com/Aureliolo/synthorg/commit/29fcd0851a4790fe9d25626a3d26890ca41908c6))
* make TokenUsage.total_tokens a computed field ([#118](https://github.com/Aureliolo/synthorg/issues/118)) ([c0bab18](https://github.com/Aureliolo/synthorg/commit/c0bab18e51c6bce227eec7ba112ba3178bd847d1)), closes [#109](https://github.com/Aureliolo/synthorg/issues/109)
* parallel tool execution in ToolInvoker.invoke_all ([#137](https://github.com/Aureliolo/synthorg/issues/137)) ([58517ee](https://github.com/Aureliolo/synthorg/commit/58517ee64a36d764142790640dfb996c9ff75100))
* testing framework, CI pipeline, and M0 gap fixes ([#64](https://github.com/Aureliolo/synthorg/issues/64)) ([f581749](https://github.com/Aureliolo/synthorg/commit/f581749ae57cb46f4fc687ab0d1f22a492593b64))
* wire all modules into observability system ([#97](https://github.com/Aureliolo/synthorg/issues/97)) ([f7a0617](https://github.com/Aureliolo/synthorg/commit/f7a0617a2659dcdc6d33447801623a879cf4c60c))


### Bug Fixes

* address Greptile post-merge review findings from PRs [#170](https://github.com/Aureliolo/synthorg/issues/170)-[#175](https://github.com/Aureliolo/synthorg/issues/175) ([#176](https://github.com/Aureliolo/synthorg/issues/176)) ([c5ca929](https://github.com/Aureliolo/synthorg/commit/c5ca92933a0cbe4b1943528150a22c529fa44f3f))
* address post-merge review feedback from PRs [#164](https://github.com/Aureliolo/synthorg/issues/164)-[#167](https://github.com/Aureliolo/synthorg/issues/167) ([#170](https://github.com/Aureliolo/synthorg/issues/170)) ([3bf897a](https://github.com/Aureliolo/synthorg/commit/3bf897a6ffde53bc940b2a993e0206d1d0bf2747)), closes [#169](https://github.com/Aureliolo/synthorg/issues/169)
* enforce strict mypy on test files ([#89](https://github.com/Aureliolo/synthorg/issues/89)) ([aeeff8c](https://github.com/Aureliolo/synthorg/commit/aeeff8ca16fdae92ec1b8fc6c8c1bc6161b64e79))
* harden Docker sandbox, MCP bridge, and code runner ([#50](https://github.com/Aureliolo/synthorg/issues/50), [#53](https://github.com/Aureliolo/synthorg/issues/53)) ([d5e1b6e](https://github.com/Aureliolo/synthorg/commit/d5e1b6ee1915bfb4c3342abd0d0e7aa79b9a1f20))
* harden git tools security + code quality improvements ([#150](https://github.com/Aureliolo/synthorg/issues/150)) ([000a325](https://github.com/Aureliolo/synthorg/commit/000a325a8a39db623d6ad397ad1d3f922e75e49e))
* harden subprocess cleanup, env filtering, and shutdown resilience ([#155](https://github.com/Aureliolo/synthorg/issues/155)) ([d1fe1fb](https://github.com/Aureliolo/synthorg/commit/d1fe1fbec2a50980efbc162e4662c373e2d166a3))
* incorporate post-merge feedback + pre-PR review fixes ([#164](https://github.com/Aureliolo/synthorg/issues/164)) ([c02832a](https://github.com/Aureliolo/synthorg/commit/c02832ac4d67aee9a19adcb4d713342f7f5bc45e))
* pre-PR review fixes for post-merge findings ([#183](https://github.com/Aureliolo/synthorg/issues/183)) ([26b3108](https://github.com/Aureliolo/synthorg/commit/26b31085e527a477bf2ebbc800929d0da743c6b2))
* resolve circular imports, bump litellm, fix release tag format ([#286](https://github.com/Aureliolo/synthorg/issues/286)) ([a6659b5](https://github.com/Aureliolo/synthorg/commit/a6659b5deb7c5f9f4a86e20e3a8728a200f3a885))
* strengthen immutability for BaseTool schema and ToolInvoker boundaries ([#117](https://github.com/Aureliolo/synthorg/issues/117)) ([7e5e861](https://github.com/Aureliolo/synthorg/commit/7e5e86189cf0229106911f4ba0f1238414edb401))


### Performance

* harden non-inferable principle implementation ([#195](https://github.com/Aureliolo/synthorg/issues/195)) ([02b5f4e](https://github.com/Aureliolo/synthorg/commit/02b5f4e742288fd644212c804395cd751d9ffc27)), closes [#188](https://github.com/Aureliolo/synthorg/issues/188)


### Refactoring

* adopt NotBlankStr across all models ([#108](https://github.com/Aureliolo/synthorg/issues/108)) ([#120](https://github.com/Aureliolo/synthorg/issues/120)) ([ef89b90](https://github.com/Aureliolo/synthorg/commit/ef89b901a86ca795ef1b58fd82c3950dbfd5b0f1))
* extract _SpendingTotals base class from spending summary models ([#111](https://github.com/Aureliolo/synthorg/issues/111)) ([2f39c1b](https://github.com/Aureliolo/synthorg/commit/2f39c1baf0de8c72911925c93ec94dc193d06916))
* harden BudgetEnforcer with error handling, validation extraction, and review fixes ([#182](https://github.com/Aureliolo/synthorg/issues/182)) ([c107bf9](https://github.com/Aureliolo/synthorg/commit/c107bf9986b54482d76f4495c9eb199e1e132f8a))
* harden personality profiles, department validation, and template rendering ([#158](https://github.com/Aureliolo/synthorg/issues/158)) ([10b2299](https://github.com/Aureliolo/synthorg/commit/10b2299989562e05868913ed90aec7e123b4dbf2))
* pre-PR review improvements for ExecutionLoop + ReAct loop ([#124](https://github.com/Aureliolo/synthorg/issues/124)) ([8dfb3c0](https://github.com/Aureliolo/synthorg/commit/8dfb3c0609ac2e9a7c3582fe7c515757f6cb6aa9))
* split events.py into per-domain event modules ([#136](https://github.com/Aureliolo/synthorg/issues/136)) ([e9cba89](https://github.com/Aureliolo/synthorg/commit/e9cba896aeb33925bba7c507fcd90729cb20f294))


### Documentation

* add ADR-001 memory layer evaluation and selection ([#178](https://github.com/Aureliolo/synthorg/issues/178)) ([db3026f](https://github.com/Aureliolo/synthorg/commit/db3026f41ea974bb85992cabb0cec722cba42f85)), closes [#39](https://github.com/Aureliolo/synthorg/issues/39)
* add agent scaling research findings to DESIGN_SPEC ([#145](https://github.com/Aureliolo/synthorg/issues/145)) ([57e487b](https://github.com/Aureliolo/synthorg/commit/57e487b1e029205cf6f733faefc50a29005b6b71))
* add CLAUDE.md, contributing guide, and dev documentation ([#65](https://github.com/Aureliolo/synthorg/issues/65)) ([55c1025](https://github.com/Aureliolo/synthorg/commit/55c102594428425882193afb80107120c93981e3)), closes [#54](https://github.com/Aureliolo/synthorg/issues/54)
* add crash recovery, sandboxing, analytics, and testing decisions ([#127](https://github.com/Aureliolo/synthorg/issues/127)) ([5c11595](https://github.com/Aureliolo/synthorg/commit/5c11595c87e61f72b0ffbfc004f9cf1c4639faf4))
* address external review feedback with MVP scope and new protocols ([#128](https://github.com/Aureliolo/synthorg/issues/128)) ([3b30b9a](https://github.com/Aureliolo/synthorg/commit/3b30b9a986a1f977092d5821e65189ed896cb63f))
* expand design spec with pluggable strategy protocols ([#121](https://github.com/Aureliolo/synthorg/issues/121)) ([6832db6](https://github.com/Aureliolo/synthorg/commit/6832db6e0d8a8295b2b1baf350e02c3f85d95cdd))
* finalize 23 design decisions (ADR-002) ([#190](https://github.com/Aureliolo/synthorg/issues/190)) ([8c39742](https://github.com/Aureliolo/synthorg/commit/8c39742b23404dc583d87ffa4611825521fb1bfc))
* update project docs for M2.5 conventions and add docs-consistency review agent ([#114](https://github.com/Aureliolo/synthorg/issues/114)) ([99766ee](https://github.com/Aureliolo/synthorg/commit/99766eee6ed9b0354cfa4d7e8dba7a6846299a74))


### Tests

* add e2e single agent integration tests ([#24](https://github.com/Aureliolo/synthorg/issues/24)) ([#156](https://github.com/Aureliolo/synthorg/issues/156)) ([f566fb4](https://github.com/Aureliolo/synthorg/commit/f566fb4bf469e119c434691c22f8894e49609a83))
* add provider adapter integration tests ([#90](https://github.com/Aureliolo/synthorg/issues/90)) ([40a61f4](https://github.com/Aureliolo/synthorg/commit/40a61f48a309d2b08797d1c840ce1d946d255d88))


### CI/CD

* add Release Please for automated versioning and GitHub Releases ([#278](https://github.com/Aureliolo/synthorg/issues/278)) ([a488758](https://github.com/Aureliolo/synthorg/commit/a4887580a2262bfd84e76c861f0106a13a438fd0))
* bump actions/checkout from 4 to 6 ([#95](https://github.com/Aureliolo/synthorg/issues/95)) ([1897247](https://github.com/Aureliolo/synthorg/commit/1897247a8bd561715639bf6dac4b136ccace7d75))
* bump actions/upload-artifact from 4 to 7 ([#94](https://github.com/Aureliolo/synthorg/issues/94)) ([27b1517](https://github.com/Aureliolo/synthorg/commit/27b15177b49357e2d9c051202b97487645cd8da5))
* bump anchore/scan-action from 6.5.1 to 7.3.2 ([#271](https://github.com/Aureliolo/synthorg/issues/271)) ([80a1c15](https://github.com/Aureliolo/synthorg/commit/80a1c157c69031d2d18beca6511edf5031e8595b))
* bump docker/build-push-action from 6.19.2 to 7.0.0 ([#273](https://github.com/Aureliolo/synthorg/issues/273)) ([dd0219e](https://github.com/Aureliolo/synthorg/commit/dd0219e27d3142cfe697f0f51beff71ad14e6c17))
* bump docker/login-action from 3.7.0 to 4.0.0 ([#272](https://github.com/Aureliolo/synthorg/issues/272)) ([33d6238](https://github.com/Aureliolo/synthorg/commit/33d6238d7d62a7c7c4902aa6a0f0108e66c7c7fc))
* bump docker/metadata-action from 5.10.0 to 6.0.0 ([#270](https://github.com/Aureliolo/synthorg/issues/270)) ([baee04e](https://github.com/Aureliolo/synthorg/commit/baee04e81d5664317a4835a0cec315316b47d6b7))
* bump docker/setup-buildx-action from 3.12.0 to 4.0.0 ([#274](https://github.com/Aureliolo/synthorg/issues/274)) ([5fc06f7](https://github.com/Aureliolo/synthorg/commit/5fc06f72c4d067cf0ea157f469e9bc0214cfc6ca))
* bump sigstore/cosign-installer from 3.9.1 to 4.1.0 ([#275](https://github.com/Aureliolo/synthorg/issues/275)) ([29dd16c](https://github.com/Aureliolo/synthorg/commit/29dd16c37ae148ff509d19627a9e884160292263))
* harden CI/CD pipeline ([#92](https://github.com/Aureliolo/synthorg/issues/92)) ([ce4693c](https://github.com/Aureliolo/synthorg/commit/ce4693ce859128e90c67beb519291ef7b4acf77e))
* split vulnerability scans into critical-fail and high-warn tiers ([#277](https://github.com/Aureliolo/synthorg/issues/277)) ([aba48af](https://github.com/Aureliolo/synthorg/commit/aba48af9d522b2d9d621955984b34abf47d6097a))


### Maintenance

* add /worktree skill for parallel worktree management ([#171](https://github.com/Aureliolo/synthorg/issues/171)) ([951e337](https://github.com/Aureliolo/synthorg/commit/951e337ce002e4756bc647d0710f483164a3d338))
* add design spec context loading to research-link skill ([8ef9685](https://github.com/Aureliolo/synthorg/commit/8ef9685f7fe5164768f206fae68970ba79f4c53f))
* add post-merge-cleanup skill ([#70](https://github.com/Aureliolo/synthorg/issues/70)) ([f913705](https://github.com/Aureliolo/synthorg/commit/f913705d04be847991854361ae1f0725623e4841))
* add pre-pr-review skill and update CLAUDE.md ([#103](https://github.com/Aureliolo/synthorg/issues/103)) ([92e9023](https://github.com/Aureliolo/synthorg/commit/92e9023c879384bb3c09cbcef0048f0a118fdcfe))
* add research-link skill and rename skill files to SKILL.md ([#101](https://github.com/Aureliolo/synthorg/issues/101)) ([651c577](https://github.com/Aureliolo/synthorg/commit/651c57772aa4f2696a41baa6ad89788e71be1f8c))
* bump aiosqlite from 0.21.0 to 0.22.1 ([#191](https://github.com/Aureliolo/synthorg/issues/191)) ([3274a86](https://github.com/Aureliolo/synthorg/commit/3274a8642e375fa0e51a215bcdf473fcf78c6515))
* bump pyyaml from 6.0.2 to 6.0.3 in the minor-and-patch group ([#96](https://github.com/Aureliolo/synthorg/issues/96)) ([0338d0c](https://github.com/Aureliolo/synthorg/commit/0338d0c42da16a0366b25c9b860d709ea5e3cc61))
* bump ruff from 0.15.4 to 0.15.5 ([a49ee46](https://github.com/Aureliolo/synthorg/commit/a49ee464ac475f3780c24902b5331509d0fb8562))
* fix M0 audit items ([#66](https://github.com/Aureliolo/synthorg/issues/66)) ([c7724b5](https://github.com/Aureliolo/synthorg/commit/c7724b55321a7d2d6b67523f95cfe43cce00f143))
* pin setup-uv action to full SHA ([#281](https://github.com/Aureliolo/synthorg/issues/281)) ([4448002](https://github.com/Aureliolo/synthorg/commit/44480022aa613f7898897a74c376b33b0dc41435))
* post-audit cleanup -- PEP 758, loggers, bug fixes, refactoring, tests, hookify rules ([#148](https://github.com/Aureliolo/synthorg/issues/148)) ([c57a6a9](https://github.com/Aureliolo/synthorg/commit/c57a6a9e619ba3339d58df221edf332998a0d1d2))
