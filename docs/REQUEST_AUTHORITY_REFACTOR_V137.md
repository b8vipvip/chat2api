# Request authority contract

The server scheduler owns API admission and releases request capacity.
The browser conversation router owns route lease lifecycle.
The background dispatch layer alone publishes dispatch failures to the server.
Model routing handles model and reasoning preparation, returning errors rather than publishing terminal events.
The content model picker owns model selection, and the reasoning controller owns reasoning selection. Both return structured evidence.
