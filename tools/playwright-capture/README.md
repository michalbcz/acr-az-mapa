# acr_capture — Playwright capture pro mapu kanálů AČR / AZ

> 🇨🇿 Česky níže je hlavní návod. 🇬🇧 English summary at the bottom.

Spustitelný Python projekt, který nahrazuje ad-hoc skripty (`capture_units.py`,
`capture_kvv.py`, `capture_experts.py`, ruční screenshoty v prohlížeči) jedním
nástrojem nad **Playwright + headless Chromium**, který se chová jako běžný
návštěvník:

- otevře mapované URL (oficiální weby, KVV, weby jednotek, Facebook / Instagram / X / YouTube / LinkedIn),
- počká na načtení (`load` + `networkidle` + selektor obsahu), zavře cookie lištu, pohne myší a pomalu proscrolluje,
- udělá screenshot (výchozí viewport 1440×900, `--full-page` volitelně),
- vytáhne poslední příspěvek, kde to jde (X, FB, IG, YouTube, weby s aktualitami),
- u přihlášených snímků rozmaže identitu prohlížejícího účtu (CSS blur v DOM + pixel blur přes `report/scripts/blur_viewer_identity.py`),
- zapíše záznamy ve **stejném formátu jako `report/data/captures.jsonl`**, takže `build_site_v2.py` funguje beze změny.

Nástroj **nikdy** neklikne na „Přidat se ke skupině“, „Sledovat“, „To se mi líbí“ apod.
Soukromé FB skupiny se jen zaznamenají (`status=private_group`), nepřidáváme se do nich.

> Tento adresář (`tools/`) je v `.vercelignore` → **nedeployuje se** na produkční web.
> Produkce dál = git push obsahu `site_v2` na `main`.

---

## 1. Instalace

```bash
cd tools/playwright-capture
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # nebo: pip install -e '.[dev]'
python -m playwright install chromium     # stáhne Chromium (+ headless shell)
# čistý Linux bez knihoven prohlížeče:
python -m playwright install-deps chromium   # (sudo)
cp .env.example .env                      # volitelné, viz níže
python -m acr_capture doctor --launch     # ověří Playwright, prohlížeč, cesty, sessions
```

Alternativa bez stahování Chromia: `ACR_BROWSER_CHANNEL=chrome` použije nainstalovaný Google Chrome.

## 2. Rychlý start

```bash
# co by se snímalo (bez prohlížeče)
python -m acr_capture targets --only social
python -m acr_capture capture --dry-run --from-captures --only x

# snímání do samostatného běhu (report se NEMĚNÍ)
python -m acr_capture capture --only social
python -m acr_capture capture --category kvv --category unit_webs
python -m acr_capture capture --url https://x.com/ArmadaCR --url https://www.youtube.com/@AZTVcz

# kompletní refresh všech kanálů z mapy a rovnou zápis do reportu
python -m acr_capture capture --from-captures --write-report
```

Každý běh vytvoří `out/run-YYYYmmdd-HHMMSS/`:

```
captures.jsonl     # záznamy (formát report/data/captures.jsonl)
screenshots/       # PNG/JPG se stejnými názvy, jaké používá report
html/              # DOM po vykreslení (pro ladění extrakce)
summary.json       # počty statusů, výsledek merge
```

### Zdroje cílů

| přepínač | význam |
|---|---|
| *(nic)* | `<report>/urls.json`, jinak přibalený `targets/urls.json` |
| `--from-captures` | všechny URL z `<report>/data/captures.jsonl` (= vše, co je na mapě) |
| `--urls-file f.json/.jsonl/.txt` | vlastní seznam (opakovatelné) |
| `--url URL` | jednotlivé URL (opakovatelné) |
| `--category social` | kategorie z `urls.json` (`official_webs`, `kvv`, `unit_webs`, `social`, `other`, `experts`) |
| `--only social\|web\|facebook\|instagram\|x\|youtube\|linkedin` | filtr podle služby |
| `--match REGEX`, `--limit N`, `--skip-groups` | další filtry |

Sociální sítě se snímají první (priorita), pak weby.

## 3. Napojení na stávající pipeline (`captures.jsonl` → `build_site_v2.py`)

`build_site_v2.py` čte `data/captures.jsonl`, deduplikuje podle URL (**poslední řádek vyhrává**)
a obrázek hledá v `screenshots/` podle názvu souboru (`find_shot(stem)`). Proto:

1. Pro URL, které už v mapě jsou, se **použije stávající název screenshotu** (např.
   `facebook-ArmadaCeskerepubliky-loggedin.jpg`) → web po buildu ukáže nový obrázek.
2. `--write-report` (nebo později `merge <run_dir>`):
   - zálohuje `captures.jsonl` → `captures.jsonl.bak-pre-playwright-<timestamp>`,
   - starý screenshot zkopíruje do `screenshots/archive/YYYY-MM-DD/` (stejně jako `backup_screenshots.py`),
   - nový screenshot nakopíruje na místo starého,
   - **připojí** sloučený záznam (zachová pole jako `officiality_hint`, `alt_platform`, sjednotí `hidden_links`).
3. **Ochrana proti zhoršení:** pokud nový běh skončí `login_wall` / `blocked` / `error`
   a předchozí záznam obsah ukazoval, starý screenshot i status zůstanou (jen poznámka
   `playwright_refresh_failed=…`). Vypnout lze `--allow-downgrade`.
   Datovaný poslední příspěvek se nepřepíše nedatovaným odhadem.
4. Pak beze změny:

```bash
cd /workspace/acr-map/report && python3 build_site_v2.py
# publikace = commit + push site_v2 jako dosud (ručně, mimo tento nástroj)
```

Doporučený postup s kontrolou: `capture` (bez `--write-report`) → prohlédnout
`out/run-…/screenshots` → `python -m acr_capture merge out/run-… --dry-run` → `merge out/run-…`.
Příklad celého denního běhu: [`scripts/daily_refresh.sh`](scripts/daily_refresh.sh).

## 4. Přihlášení (FB / IG / X / LinkedIn / YouTube) — bez hesel v repu

Nástroj pracuje s Playwright **`storage_state`** (cookies + localStorage) uloženým v
`secrets/<služba>.storage_state.json`. Adresář `secrets/` je v `.gitignore`, vytváří se s
právy `700`, soubory `600`. Při snímání se session načte automaticky; po běhu se
obnovené cookies zapíší zpět (session tak déle vydrží; vypnout `--no-refresh-sessions`).
Bez session se stránka snímá anonymně (FB pak typicky ukáže `login_wall`).

> Doporučení: používej **vedlejší / pracovní účet**, ne osobní. Session soubor je
> ekvivalent přihlášení — kdo ho má, je přihlášený jako ty.

Máš 3 možnosti — vyber si jednu pro každou službu:

### A) Jednorázové ruční přihlášení v okně (doporučeno)

```bash
python -m acr_capture login facebook     # otevře viditelný Chromium
# přihlas se ručně (vč. 2FA / „je to opravdu tvůj účet?“)
# jakmile se objeví session cookie (FB c_user+xs, IG sessionid, X auth_token, LinkedIn li_at),
# session se uloží:  secrets/facebook.storage_state.json
python -m acr_capture check-session facebook   # ověř headless
python -m acr_capture capture --only facebook  # dál už vše headless
```

Na serveru bez displeje: spusť `login` na svém počítači a soubor
přenes (`python -m acr_capture export-state facebook /cesta/facebook.json` → zkopíruj do
`secrets/` na serveru), nebo `xvfb-run -a python -m acr_capture login facebook` s VNC,
nebo použij variantu C.

### B) Přihlašovací údaje z proměnných prostředí (volitelné)

```bash
export ACR_FB_EMAIL='…'  ACR_FB_PASSWORD='…'      # nebo do .env (git-ignored)
python -m acr_capture login facebook --use-env-credentials
```

Formulář se vyplní „lidským“ psaním, okno zůstane otevřené pro 2FA. Údaje se nikam
neukládají — uloží se jen výsledná session. `--headless` jde, ale FB/IG/X automatické
přihlášení často zablokují nebo chtějí ověření; pak použij A nebo C.
Proměnné: `ACR_FB_EMAIL/PASSWORD`, `ACR_IG_USERNAME/PASSWORD`, `ACR_X_USERNAME/PASSWORD`,
`ACR_LINKEDIN_EMAIL/PASSWORD`.

### C) Import cookies z běžného prohlížeče (bez sdílení hesla)

Když nechceš nikomu dávat heslo, stačí exportovat cookies z prohlížeče, kde už přihlášený jsi:

1. V Chrome/Firefoxu se přihlas na facebook.com (instagram.com, x.com…).
2. Export cookies jedním ze způsobů:
   - rozšíření **Cookie-Editor** → *Export* → *JSON* (zkopíruje do schránky → ulož jako `fb-cookies.json`);
   - rozšíření **Get cookies.txt LOCALLY** → `cookies.txt` (Netscape formát);
   - **DevTools** (F12) → Network → libovolný požadavek na facebook.com → Request Headers → zkopíruj hodnotu `cookie:` do souboru `fb-cookie-header.txt`
     (zde je nutné `--domain .facebook.com`);
   - nebo hotový Playwright `storage_state.json` z jiného stroje.
3. Import (formát se pozná automaticky, cookies cizích domén se odfiltrují):

```bash
python -m acr_capture import-cookies facebook ~/Downloads/fb-cookies.json --delete-source
python -m acr_capture import-cookies x ~/Downloads/cookies.txt --delete-source
python -m acr_capture import-cookies instagram ig-cookie-header.txt --domain .instagram.com --delete-source
python -m acr_capture check-session all
```

Musí být v exportu i **HttpOnly** cookies (`xs`, `sessionid`, `auth_token`, `li_at`) —
Cookie-Editor i cookies.txt je exportují, `document.cookie` v konzoli **ne**.
Exportovaný soubor po importu smaž (`--delete-source`). Odhlášení v prohlížeči
session zneplatní — pro dlouhodobé použití se v tom profilu neodhlašuj, nebo použij variantu A.

### Správa sessions

```bash
python -m acr_capture sessions            # přehled (jen názvy/expirace, žádné hodnoty)
python -m acr_capture check-session all   # živé ověření, exit 2 = některá session vypršela
python -m acr_capture export-state x /bezpecne/misto/x.json
rm secrets/facebook.storage_state.json    # "odhlášení" nástroje
```

Umístění lze změnit `ACR_SECRETS_DIR` nebo pro jednotlivou službu
`ACR_FACEBOOK_STORAGE_STATE=/cesta/…json`.

### Co nikdy necommitovat

`.env`, `secrets/`, `*storage_state*.json`, `cookies*.json|txt`, `out/` — vše je v `.gitignore`.
Před commitem si ověř `git status` — v diffu nesmí být žádný cookie/heslo.

## 5. Emulace reálného uživatele

- Chromium v „new headless“ režimu (`ACR_BROWSER_CHANNEL=chromium`), UA běžného desktop Chrome
  bez `HeadlessChrome`, `navigator.webdriver` skrytý, `--disable-blink-features=AutomationControlled`.
- Viewport 1440×900, `cs-CZ`, časová zóna Europe/Prague, světlý režim, `Accept-Language` cs.
- Čekání na `load` + `networkidle` (u FB/X je timeout normální – long polling) + selektor obsahu.
- Pohyb myší, pozvolné scrollování dolů a zpět (načte lazy obsah), náhodné pauzy mezi stránkami
  (`ACR_DELAY_MIN/MAX`, výchozí 2–6 s). Jedna session/kontext na službu.
- Cookie lišty: výchozí *odmítnout nepovinné* (`ACR_CONSENT=reject|accept|skip`).
- Cílem je vidět stránku jako běžný návštěvník, ne obcházet CAPTCHA či omezení přístupu —
  při `blocked` nástroj nic nezkouší obejít, jen to zaznamená.

## 6. Ostatní příkazy

```bash
python -m acr_capture --help
python -m acr_capture capture --help
python -m acr_capture blur screenshots/x.png --url https://x.com/ArmadaCR   # ruční blur
python -m pytest -q                                                         # offline testy
```

| příkaz | popis |
|---|---|
| `doctor [--launch]` | kontrola prostředí |
| `targets` | seznam cílů + zda je známe v mapě, název souboru, session |
| `capture` | snímání (`--dry-run`, `--headed`, `--full-page`, `--format jpg`, `--blur auto/always/never`, `--anonymous`, `--write-report`) |
| `merge <run_dir>` | sloučení dřívějšího běhu do reportu (`--dry-run`, `--only-ok`) |
| `login <svc>` | ruční / env přihlášení → storage_state |
| `import-cookies <svc> <soubor>` | cookies z prohlížeče → storage_state |
| `sessions`, `check-session`, `export-state` | správa sessions |
| `blur` | rozmazání identity na existujících screenshotech |

## 7. Struktura

```
tools/playwright-capture/
├── acr_capture/
│   ├── cli.py        # argparse příkazy (python -m acr_capture …)
│   ├── config.py     # nastavení z env / .env
│   ├── services.py   # FB/IG/X/LinkedIn/YouTube: domény, login URL, session cookies, selektory, zákaz „join/follow“
│   ├── browser.py    # spuštění Chromia, realistický kontext, cookie lišty, scroll, CSS blur identity
│   ├── auth.py       # login (ruční / env) → storage_state, check-session
│   ├── cookies.py    # import cookies (Cookie-Editor JSON, cookies.txt, Cookie header, storage_state)
│   ├── capture.py    # hlavní smyčka snímání
│   ├── extract.py    # poslední příspěvek (port z capture_units.py + DOM extraktory), skryté odkazy, status
│   ├── records.py    # kompatibilita s captures.jsonl, merge + zálohy
│   ├── targets.py    # načítání/filtry cílů
│   └── blur.py       # volá report/scripts/blur_viewer_identity.py (fallback přibalen)
├── targets/urls.json # kopie report/urls.json (fallback, když report není k dispozici)
├── scripts/daily_refresh.sh
├── tests/            # offline testy (pytest), fixtures s FALEŠNÝMI cookies
├── .env.example  ·  requirements.txt  ·  pyproject.toml  ·  .gitignore
```

Poznámka k webům: poslední příspěvek z webů je heuristika (port z `capture_units.py`);
podrobnější data z podstránek aktualit dál dodává `enrich_sites.py` / `enrich_refine.py`
(`data/enrichment.json`) — merge datovaný příspěvek nikdy nepřepíše nedatovaným.

Stávající skripty v `report/scripts/` zůstávají beze změny a dál fungují; tento nástroj je
přidaná cesta, ne náhrada, dokud se neosvědčí.

---

## 🇬🇧 English summary

**What:** a Playwright + headless Chromium CLI (`python -m acr_capture`) that opens every
mapped URL like a real desktop user (realistic UA/viewport/locale, waits for load +
network idle + content, dismisses cookie banners, mouse + gentle scrolling, random
delays), screenshots it, extracts the latest post where feasible (X, Facebook, Instagram,
YouTube, news sections on webs), blurs the logged-in viewer's identity, and writes
records in the exact `report/data/captures.jsonl` schema so `build_site_v2.py` keeps working.
It never clicks join/follow/like and does not join private Facebook groups.

**Install:** `pip install -r requirements.txt && python -m playwright install chromium`
(`install-deps` on bare Linux; or `ACR_BROWSER_CHANNEL=chrome`). Check with
`python -m acr_capture doctor --launch`.

**Run:** `python -m acr_capture capture --only social` (separate run dir under `out/`),
`--from-captures --write-report` to refresh everything and merge into the report
(backs up `captures.jsonl`, archives replaced screenshots, never downgrades a good
capture to a login wall unless `--allow-downgrade`). Then `python3 build_site_v2.py` as before.

**Login without secrets in git** — sessions are Playwright `storage_state` files in
`secrets/` (git-ignored, chmod 600):

1. `python -m acr_capture login facebook` — headed window, log in by hand (2FA ok), session is saved;
   afterwards everything runs headless. No display? Run it on your desktop and copy the file
   (`export-state`), or use option 3.
2. `login facebook --use-env-credentials` — prefill from `ACR_FB_EMAIL` / `ACR_FB_PASSWORD`
   (env or `.env`); credentials are never written to disk.
3. `import-cookies facebook cookies.json --delete-source` — export cookies from your normal
   browser (Cookie-Editor JSON, cookies.txt, a raw `Cookie:` header with `--domain`, or a
   storage_state). No password is shared. HttpOnly cookies must be included.

Verify with `check-session all`; list with `sessions` (never prints values). Never commit
`.env`, `secrets/`, cookie exports or `out/` — all are in `.gitignore`. Use a secondary account.

`tools/` is excluded from the Vercel deployment via the root `.vercelignore`.
