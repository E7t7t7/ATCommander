# AT Commander 1.7.23

## Safe Swole Protocol Lab

This build adds a one-click **RUN SAFE SWOLE PROTOCOL LAB** diagnostic under **Diagnostics / Learn**.

The lab is designed to answer the remaining signed-client question without spending Swole energy or exporting account secrets:

- Reuses the already-learned Swole Fight-button position in the dedicated mini browser.
- Arms Chromium Fetch interception at the **request** stage.
- Lets the real AT client generate a normal signed `startChallenge` request.
- Captures safe request metadata and the browser initiator/call stack.
- Blocks the request before it is transmitted to AT, so no fight should be created and no Swole energy should be consumed.
- Reads only bounded source-code excerpts around the real caller plus occurrences of `fetchWithProgress`, `clisig`, `msgsig`, and `startChallenge` from the currently loaded game scripts.
- Writes `protocol_lab_swole_sanitized.json` into AT Commander's local data directory.

The exported report deliberately does **not** contain the user's password/login hash, Kong token, cookies, authorization data, session values, or the actual `clisig` / `msgsig` values. Signature fields are represented only by presence/length metadata.

The existing 1.7.22 Swole/Challenge automation remains unchanged in this build. The Protocol Lab is diagnostic only and is intended to replace repeated trial-and-error fights with one safe, inspectable capture.
