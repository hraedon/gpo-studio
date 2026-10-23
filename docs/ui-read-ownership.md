# UI read ownership

`static/js/read-context.mjs` owns a small, explicit UI lease: intent, editor
opening, visible owner, dialog session, and a caller's domain predicate. Call
`start` for a new request intent, or `capture` for a chain that shares intent;
`invalidate` retires it immediately when input changes, before any debounce.
Tab activation and admitted policy-selection dispatch invalidate read-view scopes.
Selection intent is observed before its full payload arrives, while the prior
policy can still be visible. Captures are not created in the
transport. They do not authorize writes or admit cached data.

`isCurrent()` gates rendering, chaining, focus, byte delivery and pending-control
finalizers. `reportError(error)` additionally composes the captured error-report
version with an optional local sink. Clearing rows or review selections in a
failure belongs **inside** that sink. A newer warning or explicit Dismiss retires
error/focus publication only; useful successful data can still be admitted.
Automatic successful-action resolution retains a current read's error lease.

Catalogue token/epoch and collection/deletion observation admission stay separate.
Health/category results can enter a valid catalogue cache after their view retires,
but cannot paint, chain another read or report for that retired view. Collection
summaries can still update the sidebar without changing an unrelated editor or
selection. A dispatched write retains its named mutation outcome even when its
originating read retires. In particular, GPP restore separates the historical
lookup from the subsequently dispatched restore write.

## Semantic caller inventory

[ui-request-ownership.json](ui-request-ownership.json) is the maintained inventory:
51 calls (one transport, 20 actual writes, 30 reads/previews/artifacts), with
semantic IDs adjacent to each call. Read entries describe owner, error sink,
retirement, cache admission and an actual-page or specialized unit test adapter.
POST previews are reads; HTTP method is not the classification rule.

| Reads | Count | Policy |
| --- | ---: | --- |
| ADMX health/search/detail/categories | 4 | Shared scope plus existing catalogue/query guards |
| Settings health/tree/configured | 3 | Shared scope; cache admission separate from owned paint and leased clearing |
| Catalogue monitor | 1 | Explicit silent cache-only epoch/token policy |
| Policy list/detail/recovery | 3 | Specialized existing request/editor/deletion provenance and revision floors; captured error lease |
| Policy History | 1 | Shared visible-panel scope plus GUID/revision/deletion/request |
| Comparison selectors/revisions/estate | 3 | Shared panel/input scope; POST estate comparison is a read |
| Conflict current/diff | 2 | Shared error/view lease composed with exact opening, consent and revision floors |
| WMI catalogue | 1 | Shared opening/request scope, inline sink |
| GPP item history lookup | 1 | Shared pre-write scope; post-dispatch outcomes remain mutations |
| GPMC inbox capabilities | 1 | Shared dialog lease plus existing editor context |
| Reviewed artifact bytes | 1 | Specialized exact review action and revision/hash binding, composed error lease; response/body/digest/delivery/finalizer guards |
| Publication plan | 1 | Shared scope plus target/GUID/revision/deletion predicates |
| RSOP and security-template POST previews | 2 | Shared opening/request/input scope, local form sink |
| Folder-redirection parse/diff | 2 | Shared scope plus file generations, including asynchronous file reads |
| Scripts preview/download | 2 | Shared scope plus rows/GUID/revision/deletion; response body and delivery/finalizer ownership |
| Starter library | 1 | Shared lease plus existing session/request/deletion admission |
| ADMX template sources | 1 | Shared scope; catalogue admission independent of retired dialog |

The `report.txt` link is intentional browser navigation: no JavaScript error sink.
Reviewed export links are intercepted by the registered review action. Blob anchors
are delivery continuations, not separate unowned requests. The API transport stamps
response provenance only; it does not publish failure UI.

## Completeness and behavioral evidence

`tests/frontend/request-inventory.test.mjs` uses the installed ESLint parser to
compare discovered calls against the registry, including semantic ID, file,
owning function, channel, method and path expression. Adding an unclassified
call, changing a registered caller or leaving a removed entry fails. It resolves
named API import aliases, API namespaces, simple identifier aliases and direct or
global `fetch`; known new network constructors/beacon fail until explicitly
supported. No line numbers are identities and no dependencies were added.

This syntax check does not prove behavioral ownership and does not follow arbitrary
wrappers, computed aliases, runtime injection, worker code or arbitrary navigation
dataflow. New transport patterns require review and extending discovery. The
registry is a review aid, not an exemption for an unsafe sink. Actual-page tests
initiate requests through real UI controls, retain active HTTP/network refusal
controls, and exercise retired warnings/editors/tabs/dialogs/inputs and successful
cache admission. Artifact tests cover delivery and finalizer ownership separately.
Specialized list, conflict, catalogue and export tests retain their stronger domain
invariants rather than replacing them with generic visibility checks.
