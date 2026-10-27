# AT Commander 1.7.21 changes

## Battle helper no longer needs to sit on the desktop

The successful 1.7.20 live tests proved Swole and Challenge/Clash/Melee starts work when the real AT client generates the signed Fetch request. 1.7.21 keeps that exact signed-client path. After a fresh live fight capture, Commander moves the dedicated Edge app window off-screen instead of leaving it over the user's desktop. Chromium is launched with background-throttling/occlusion protections so Unity and signed Fetch generation can keep running. `OPEN MINI GAME` brings the helper back on-screen when login, navigation, or another manual capture is needed.

The Edge process is still required because AT generates fresh `client_time`, `clisig`, and `msgsig` inside the real game client. The visible window is not.

## Reddit consensus fallback

Reddit's anonymous JSON search frequently returns 403/429 to local desktop clients. 1.7.21 keeps JSON search first, then automatically falls back to Reddit's public Atom/RSS subreddit search. Atom entries are converted to the same review-post representation, so the current scorer and Discord bot share identical consensus logic.

The parser also recognizes common recent review formats such as `Wingnut Amy - 6.0 - ...`, `Rating: 6.5/10`, explicit historic score rows, and prose such as `I'll score it a 3`. Community evidence remains secondary and recency-weighted; current AT card values remain the foundation.

## Confirmed from 1.7.20 live testing

- Swole live-game signed Fetch start: confirmed working.
- Challenge/Clash/Melee live-game signed Fetch start: confirmed working.
- Wingnut Amy current-power separation: 1.0/10 in the user's test.
- Buckarito current-power separation: 6.5/10 in the user's test.
