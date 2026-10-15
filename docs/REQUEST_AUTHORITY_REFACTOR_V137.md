# Request authority contract

The server scheduler owns API admission and releases request capacity.
The browser conversation router owns route lease lifecycle.
The background dispatch layer alone publishes dispatch failures to the server.
Model routing handles model and reasoning preparation, returning errors rather than publishing terminal events.
The content model picker owns model selection, and the reasoning controller owns reasoning selection. Both return structured evidence.

Reasoning selection is successful only when page-Driver verification succeeds; warnings cannot be reported as a successful applied effort. Unsupported extra-high requests fail explicitly rather than being silently submitted at high while retaining extra-high diagnostics.
