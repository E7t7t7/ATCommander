# AT Commander 1.7.35 — game client 1.184 / Unity patch verification

The official current build Throwdown_WebGL_v1.184.0_r41124 uses Unity
6000.3.27f2. The checker required 6000.3.6f1 and rejected it before inspecting
its otherwise unchanged signing code. The actual official assets were inspected
offline: hash, version-number, Unity-field helper and request-signer fingerprints
all match the previously verified native structures exactly. The correct profile
is client_version 1184000, full version 1.184.0, platform Web, Unity6000_3_27.
That exact build/profile is now included.

Automatic verification derives the Unity request tag from the inspected engine
patch within the supported 6000.3 release family. It still requires the exact
known native helper and signer fingerprints, bound to their literals/types/
methods, with one unique matching sender. Other families, layouts, changed
signers and malformed assets remain rejected. This supports compatible engine
patch releases without treating every patch number as a signing change.
Verified-cache records are validated against their actual engine tag; modern
verified records no longer trigger needless native-asset redownloads.

Failure text now reports an allowlisted public reason (for example a changed
request signer or public-download HTTP status) rather than the blanket generic
failure. Arbitrary exception text/private material is never echoed. The previous
profile is preserved on failure. A verifier revision change triggers one new
automatic check after launch even if an older checker already attempted today.
Normal once-per-day and manual-check behavior remain unchanged.

Validation: all 1,455 checks pass. The complete unknown-build verification path
was also exercised against the live official launcher and the actual downloaded
1.184 assets, producing and loading a valid engine-specific profile. Downloaded
native code was parsed, never executed. No account requests or fights were sent.

Public asset SHA-256 evidence:
- WASM: b8b4a785cfe72b5f7ca62115e76caa8d4661eae89ef1e8bb9deb91abdc3a59df
- Metadata: af057d4a540e657aa25463a0554215941f98d2ed3e64815e6e91c42d6c045ca1
