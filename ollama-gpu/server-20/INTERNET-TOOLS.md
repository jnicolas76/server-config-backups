# Current Time and Internet Tools

The tools run in the existing OpenAPI gateway on server `.20`; Ollama on `.232` remains a dedicated
inference server. Open WebUI supplies the tool descriptions and results to `qwen3:4b` or
`llama3.2:3b` during chat.

Operations:

- `currentDateTime`: local Denver time, UTC time, and Unix timestamp.
- `searchInternet`: up to eight current public search results with title, URL, and snippet.
- `readInternetPage`: extracts at most 30,000 text characters from a public webpage.

Safety controls:

- HTTP/HTTPS only.
- URLs containing credentials are rejected.
- DNS destinations must resolve exclusively to globally routable addresses.
- Private, loopback, link-local, multicast, and reserved targets are rejected.
- Fetches use a 15-second timeout and read at most 1 MB.

Open WebUI connection name: `Current Time, Internet & Home Network`.
