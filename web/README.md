# Web UI

React + TypeScript (Vite) front end for the tutor's FastAPI app. See the main README for
running it; in short:

```sh
uv run spanish-tutor serve   # the API (and the built UI) on http://127.0.0.1:8000
cd web
npm install
npm run dev                  # the UI with hot reload on http://localhost:5173, proxying /api
```

- `npm run build` type-checks and writes `dist/`, which `spanish-tutor serve` then serves.
- `npm test` runs the component tests (Vitest + Testing Library); `npm run lint` runs oxlint.
- `npm run gen:api` regenerates `src/api/schema.d.ts` from the API's OpenAPI schema after
  an API change, so the UI's types always match the server.
