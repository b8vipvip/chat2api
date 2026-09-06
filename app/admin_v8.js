(() => {
  // v97: Request History ownership is retired at source.
  //
  // The historical v8 asset used to append a second “日志” <th> and replace
  // loadRequests with a 10-cell renderer. Newer request-history versions have
  // more columns, so allowing this legacy code to execute caused the header and
  // row values to shift and also hid the request ID. Diagnostics controls and
  // per-request log downloads are now provided by the canonical request-history
  // owner in request_history_v94_patch.py.
})();
