# Entertainment Shell — Build Plan

> Target: the full-screen Entertainment experience shown to installs that have an
> Entertainment provider plugin (AniCLI and/or PandaFlix). This plan is written to
> be handed to a fresh session; it contains no secrets and no environment-specific
> values.

## Goal

Replace the current minimal Entertainment overlay (a small header, a plain hero, and
flat result lists) with the branded streaming shell in the approved mockups:

- **Landing** — branded choice screen ("What are we watching tonight?") with two large
  provider cards.
- **Anime** — AniCLI browse screen: hero banner, feature chips, boxed search panel, and
  a poster-card results grid.
- **Movies & Shows** — PandaFlix browse screen: same shell, movie/TV copy and "View
  Seasons" / "Watch Now" actions.

The shell appears only when at least one provider plugin is installed and enabled
(existing `refreshEntertainment()` gate).

## Current state (what exists)

- Modal: `static/index.html` `#entertainment-modal` (~line 3155).
- Logic: `static/js/entertainment.js` (search, episodes, seasons, resolve, play,
  favorites, continue-watching, prefs).
- Styles: `static/style.css` `.entertainment-*` block (~line 44494).
- Providers: adapters in `integrations/repository-packages/ani-cli` and
  `integrations/repository-packages/pandaflix`.
- Routes: `routes/entertainment_routes.py`, `src/entertainment.py`.
- Tests: `tests/test_entertainment.py`, `tests/browser/entertainment.spec.js`.

Preserve every existing element id used by the JS (`entertainment-landing`,
`entertainment-browser`, `entertainment-player`, `entertainment-search`,
`entertainment-query`, `entertainment-mode`, `entertainment-quality`,
`entertainment-results`, `entertainment-saved`, `entertainment-resume`,
`entertainment-favorites`, `entertainment-jump`, `entertainment-tabs`,
`entertainment-next`, `entertainment-autoplay`, `entertainment-fullscreen`, etc.) so
the search/resolve/play flows keep working while the markup is restyled.

## Shell structure (inside `#entertainment-modal`)

```
ent-topnav        panda logo + PANDAMONIUM wordmark; Home / Anime / Movies & Shows /
                  My List / Downloads; global search (⌘K); avatar menu
ent-body
  ent-sidebar     BROWSE (Anime, Movies & Shows, Genres, Calendar, Popular,
                  Top Rated, Recently Added) + MY LIBRARY (Watchlist, History,
                  Downloads) + mascot ("BUILD WATCH REPEAT")
  ent-content     landing | browser | player
ent-footer        PANDAMONIUM vX • Private Runtime • Built for Builders
                  Good Anime. Better People. — MADPANDA3D
```

## Assets needed from the operator

Final art (placeholders are used until supplied):

| # | Asset | Use | Format / size |
|---|-------|-----|---------------|
| 1 | Panda logo, **transparent** | top nav, watermark | SVG or PNG with alpha, ≥512px |
| 2 | Panda mascot (dark, glowing, "BUILD WATCH REPEAT") | sidebar | PNG alpha, ≥600×900 |
| 3 | Landing background (panda watermark + lava/rock landscape) | landing | 2560×1440+ (or two layers) |
| 4 | Anime choice-card art | landing card | ~3:4 portrait, 800×1000 |
| 5 | Movies & Shows choice-card art | landing card | ~3:4 portrait, 800×1000 |
| 6 | Anime hero banner | anime hero | ~2560×720, dark left third |
| 7 | Movies & Shows hero banner | movies hero | ~2560×720, dark left third |
| 8 | (Optional) generic poster fallback | missing covers | 2:3, 500×750 |

Result covers are dynamic (not static assets) once a metadata source is wired.

## Metadata + covers (the real dependency)

The mockup cards show cover art, type (TV Series / Movie / Special), year, genres, and
a description. Today:

- ani-cli search returns only `id` + `title`.
- PandaFlix `SearchResult.Poster` is only populated by its TMDB provider; the streaming
  providers leave it empty, and `--show-image` renders to the terminal via chafa and
  never emits a URL.

Decision needed (recommended):

- **Anime → AniList public GraphQL** (no API key): `coverImage`, `format`, `seasonYear`,
  `genres`, `description`. Reliable, purpose-built.
- **Movies & Shows → PandaFlix machine-readable posters**: add a `--json`/poster output
  to the pinned pandaflix repo (MADPANDA3D/pandaflix), populate `Poster` for the
  providers in use, request thumbnails in the allanime query, bump the pinned revision.
- Serve all remote images through an **SSRF-safe image proxy** (same bounded pattern as
  the existing media proxy); never let the browser hit arbitrary hosts.

If the operator does not want the metadata work yet, Phase 1 ships layout + placeholders
only and cards show title + a generated placeholder cover.

## Phases

1. **Shell + landing (placeholder art)** — top nav, sidebar, footer, landing choice cards,
   branded copy and feature chips. Direct-push to CT103 for approval.
2. **Browse screens (placeholder covers)** — anime/movies hero, feature chips, boxed
   search panel (Type / Quality / Language, Go-to-episode, Random), poster-card grid,
   sort + grid/list toggles. Keep search/episodes/resolve/play working.
3. **Metadata + covers** — AniList for anime; PandaFlix poster output for movies; image
   proxy; card badges (type/year/genres) and descriptions; favorite-on-card.
4. **Shell nav** — wire Home / Anime / Movies & Shows / My List / Downloads, sidebar
   Browse/My Library entries, global search, avatar menu (functional where data exists,
   visually disabled otherwise).
5. **Polish** — responsive breakpoints, reduced-motion, focus/keyboard states, empty and
   error states, service-worker cache bump.

## Review + release workflow

- Build locally in a worktree based on `fix/entertainment-solo-tab` (which already has the
  solo sidebar-tab fix) so nothing regresses.
- **Direct push** changed `static/*` files into `/opt/pandamonium/releases/<current>/`
  on CT103 for operator review (no update, no restart; bump the SW `CACHE_NAME`).
- After operator approval, commit on `feat/entertainment-shell`, open a PR, and let the
  next signed release carry it. Direct-pushed files are replaced by that release.

## Acceptance criteria

- Shell appears only when a provider plugin is installed/enabled.
- Landing matches the approved mockup composition (with placeholder art acceptable in
  Phase 1/2).
- Anime and Movies & Shows screens match the mockup structure; search, episode/season
  selection, playback, favorites, and continue-watching all still work.
- No provider, route, dependency, schema, auth, or stored-data change in the layout-only
  phases.
- Keyboard and mobile usable; no horizontal overflow at 1366×768, 1920×1080, 3840×2160.

## Open questions

1. Final art assets (table above) — provided, or placeholders for now?
2. Metadata source: approve AniList (anime) + PandaFlix poster output (movies)?
3. Which shell nav items are functional in v1 vs visual-only?
4. Landing hero copy locked to the mockup ("What are we watching tonight?", etc.)?
5. Footer version string should read the live `APP_VERSION`.

## Next — title detail + hover (Crunchyroll reference, 2026-09-20)

Operator supplied Crunchyroll screenshots as the target for these:

1. **Result-card hover**: reveal a description plus three actions — **Save**
   (favorite), **Add to Watchlist**, and **Play**.
2. **Title detail page**: clicking a title opens an anime detail view with a
   **hero section** for the show and an **episode list with video thumbnails**,
   progress indicators ("2m left" / "Watched"), and a play overlay.
3. **Per-user History**: `prefs.history` now records provider/title/episode/
   position per user and the History page lists it and resumes on click. Follow-up:
   add episode thumbnails, "Xm left"/"Watched" state, and a clear-history action
   (see Crunchyroll "My Lists → History").
4. **Watchlist**: separate list from Favorites; add an "Add to Watchlist" action.

These need the metadata/thumbnail source (AniList for anime, pandaflix/TMDB for
movies) and an episode-thumbnail URL, so they follow the metadata decision.
