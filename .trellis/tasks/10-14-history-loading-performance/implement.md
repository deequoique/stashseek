# Execution plan

1. Review the persisted task artifacts before starting implementation.
2. Backend implement agent: eliminate auth ORM-expiration reloads; slim transcript
   projection; add query-count/payload/isolation tests and run focused checks.
3. Frontend implement agent: route splitting and authenticated history preload;
   deduplication, privacy, disabled/failure and delayed-request regression tests.
4. Main agent: integrate, inspect bundles and generated contracts, run required
   frontend gates and affected/full Python checks with test-only configuration.
5. Check agent: review the complete change against task/specs and fix findings.
6. Browser smoke: opening the site, protected history, route navigation/refresh,
   history selection and console cleanliness using the isolated local demo.
7. Update specs and task results with measured evidence and live-test limits.
8. Present a concrete commit plan for confirmation; no push/deployment.
