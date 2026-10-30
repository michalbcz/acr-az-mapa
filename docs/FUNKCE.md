# Mapa kanálů AČR / Aktivní zálohy – katalog funkcí

> Stav k 30. 10. 2026. Odvozeno ze skutečného kódu (`pipeline/build_site_v2.py`, `monitoring_ui.py`, `events_ui.py`,
> `pipeline/scripts/*.py`) a z [SPECIFIKACE.md](SPECIFIKACE.md). Dokument popisuje **co** projekt dělá (data a funkce),
> ne design ani UX. Je určen jako zadání: agent, který ho dostane, má být schopen obsahově vytvořit totéž.
> Podrobné formáty polí jsou v SPECIFIKACE.md (kap. 7 a Příloha A).

Živý web: <https://mapa-acr-az.vercel.app>

## Obecné zásady (platí pro všechny funkce)

- **Nic se nevymýšlí.** Každý příspěvek, datum, odkaz, událost i ověření pochází z otevřeného zdroje, který byl skutečně načten. Prázdný stav je přípustný.
- **Zdroj = konkrétní příspěvek** (permalink), ne profil/účet. Když permalink nelze doložit, zůstane odkaz na účet – ID se nikdy nedomýšlí.
- **Statický výstup**: jedna stránka `index.html` + JSON + ICS + obrázky; žádný backend, přihlášení ani cookies. Jazyk češtiny.
- **Časy** se ukládají absolutně (ISO, Europe/Prague); relativní text („před 2 h“) počítá až prohlížeč.
- **Soukromí**: soukromé osoby jsou anonymní, rozmazává se jen identita účtu, přes který se prochází, žádná interakce se sítěmi.
- **Zápis dat jen přes validační skripty** (komentáře, události, Zpráva dne), které odmítnou nevalidní vstup.
- **Denní frekvence**: celý povinný běh jednou denně ráno (cca 8:00, Europe/Prague), cíl < 40 min.

---

## F1. Registr sledovaných kanálů

**Účel.** Seznam všech komunikačních kanálů kolem AČR, MO, AZ, KVV, jednotek AZ, dobrovolníků/partnerů a odborných médií.

**Data.** `urls.json` (skupiny `official_webs`, `kvv`, `unit_webs`, `social`, `other`, `experts`) + `data/captures.jsonl` (1 řádek = 1 kanál; aktuálně 108 kanálů).
Typy zdrojů: weby (MO, AČR, KVV, útvarové weby, spolky, odborná média), Facebook stránky, FB skupiny, Instagram, YouTube, X, LinkedIn, fóra, jiné platformy (Discord, WhatsApp – jen evidence).

**Atributy kanálu.** `url`, `slug`, screenshot, `last_post_title/author/date/url`, `status`, `hidden_links[]` (nalezené odkazy na další kanály: `url`, `type`, `source`), `notes`, `captured_at`, volitelně `recent_posts[]`, `alt_platform`.

**Pravidla.** Při duplicitě URL vyhrává poslední záznam. Přejmenované URL se mapují přes `data/url_aliases.json` (jen pro zobrazení). Soukromé skupiny se jen evidují, nikdy se do nich nevstupuje.

**Akceptace.** Každý kanál má právě jednu dlaždici; nové odkazy nalezené v HTML webů se objeví jako `hidden_links` v detailu.

## F2. Kategorizace kanálů

**Účel.** Zařadit každý kanál do jedné kategorie (pořadí = pořadí sekcí na stránce), funkce `categorize(url)` podle hostu/cesty.

| klíč | název | obsah |
|---|---|---|
| `oficialni` | Oficiální weby MO / AČR | centrální weby ministerstva a armády |
| `kvv` | KVV — krajská velitelství | Aktivní záloha u KVV |
| `jednotky` | Jednotky AZ / útvarové weby | weby praporů a rot |
| `dobrovolnici` | Dobrovolníci a partneři | ARDOS, POKOS, SVZ, ČsOL, RESCO… |
| `facebook` | Facebook stránky | veřejné stránky AČR / AZ |
| `fb_groups` | FB skupiny | veřejné i soukromé, jen evidence |
| `instagram` | Instagram | oficiální a AZ účty |
| `youtube` | YouTube | TV Armáda, AZTV |
| `x` | X (Twitter) | ArmadaCR, ObranaTweetuje |
| `linkedin` | LinkedIn | firemní stránky MO / AČR |
| `fora` | Fóra a diskuze | neoficiální vojenská fóra |
| `odbornici` | Odborná média a analytici | czdefence.cz, armadninoviny.cz, natoaktual.cz, securityoutlines.cz, valka.cz… a jejich sociální účty (testuje se jako první) |
| `alt` | Jiné platformy | Discord, WhatsApp (evidence) |

**Akceptace.** Každá kategorie má sekci s počtem kanálů a přehled (`#overview`) se souhrnem kategorií.

## F3. Odznaky oficiálnosti

**Účel.** Odlišit oficiální kanály státu od ostatních. `officiality(url)` vrací štítek **Oficiální** nebo **Neoficiální**.

**Pravidla.** Oficiální = každý host `*.mo.gov.cz` + výčet ověřených sociálních účtů (FB `armadaceskerepubliky`, `ministerstvoobranyceskerepubliky`, `doarmady.cz`, `aktivnizalohy`, `43vysadkovypluk`; IG `armadaceskerepubliky`, `do_armady`; YT `@tvarmycz`; X `armadacr`, `obranatweetuje`; LinkedIn `ministerstvoobrany`, `czecharmy`, `czech-army`). Vše ostatní je neoficiální (skupiny, `profile.php`, weby jednotek a spolků, odborná média). Neoficiální médium se nikdy neoznačí jako oficiální (ani ve Zprávě dne).
Třetí úroveň „polooficiální“ **není implementována** (jen návrh).

**Akceptace.** Každá dlaždice nese právě jeden odznak.

## F4. Snímky obrazovky (capture) s rozmazáním identity procházejícího účtu

**Účel.** Vizuální doklad stavu kanálu v den sběru.

**Zdroje/metoda.** Weby: HTTP + headless prohlížeč. FB/IG/LinkedIn: prohlížeč (případně přihlášený), jen čtení. Z každého snímku se vytvoří náhled (`thumbs/`) a plné rozlišení (`full/`) pro lightbox.

**Pravidla.**
- Před přepsáním se snímek zálohuje do `screenshots/archive/YYYY-MM-DD/` (`backup_screenshots.py`); sociální sítě se snímají každý den znovu.
- Rozmazává se **jen identita prohlížecího účtu** (vlastní avatar/jméno/handle, přepínač účtů, pole „Napsat komentář“ s avatarem, profilová ikona prohlížeče, „Followed by … you follow“, avatary přátel, bublina zpráv). Jména a avatary ostatních lidí se nerozmazávají. Nástroj `blur_viewer_identity.py` (OCR + pevné oblasti), výsledek se kontroluje okem.
- Nerozmazané snímky a surové capture se nikdy nepublikují ani necommitují.

**Akceptace.** Každý publikovaný snímek sociální sítě neobsahuje identitu procházejícího účtu; dlaždice bez snímku se vykreslí bez obrázku.

## F5. Poslední příspěvek kanálu + odkaz „zdroj“ na konkrétní příspěvek

**Účel.** Ukázat, co kanál naposledy publikoval.

**Data.** Titulek/text, autor, datum, `last_post_url` (permalink). Zdroje: HTML webu/novinkové podstránky, RSS (czdefence.cz `rss2`), X (syndication timeline, při HTTP 429 X API v2 `get_users_posts` se `since_id` → `x_posts.json`, `x_tweet_index.json`), FB/IG/YT/LinkedIn stránky.

**Pravidla.**
- `last_post_url` se ukládá vždy spolu s titulkem; při změně titulku se URL aktualizuje nebo smaže, nikdy nezůstane stará.
- `social_permalinks.py` dohledá permalink (X `/status/<id>`, FB `/posts/…`, `/reel/…`, IG `/p/<code>/`, YT `watch?v=`) z indexu (X cache, x_posts, captures, komentáře); jinak odkaz na účet.
- Relativní data ze sítí (`1h`, `2d`) se nahradí absolutním datem z historie (`_fix_relative_social_date`); relativní `date_raw` se nepublikuje.

**Akceptace.** Odkaz u příspěvku vede na permalink, kde existuje; žádné vymyšlené ID.

## F6. Metadata webů (enrichment)

**Účel.** Technický profil každého webu. Skripty `enrich_sites.py`, `enrich_refine.py` → `data/enrichment.json` (aktuálně 55 webů).

**Atributy.** HTTP `Last-Modified`, `ETag`, `Date`, `html_modified`, `meta_article_time`, `cms` + `cms_version` + `cms_evidence`, `admin_urls[]` a `admin_checks[]` (jen HTTP stav – HEAD/GET, žádné přihlašování), `news_pages[]` (novinkové podstránky s posledním článkem, datem, URL, případně `items[]`), `fetch_status`, `final_url`, `error`, `feed_source`.

**Akceptace.** Detail kanálu zobrazí CMS, důkaz, admin URL s ověřeným stavem a novinkové podstránky.

## F7. Historie příspěvků (archiv) s deduplikací

**Účel.** Trvalý seznam všech viděných příspěvků; základ pro chronologii, feed, histogram a počet „archiv N“ v hlavičce.

**Data.** `data/updates_history.jsonl` (append-only): `id` (hash kanálu+textu), `url`, `post_url`, `source_label`, `category`, `title`, `author`, `date_raw`, `date_iso`, `kind`, `urls[]`, `captured_at`, `first_seen`, `last_seen`. Plní `archive_updates.py` (spouští ho builder na začátku) a `social_permalinks.py --backfill`.

**Dedup.** Kanál (sociální: host + první segment cesty; web: host) + normalizovaný text (malá písmena, sjednocené mezery, bez koncového „…“, prvních 120 znaků). Shoda jen posune `last_seen` a doplní `urls`. Datum = skutečné absolutní datum, jinak první viděné. Stejný text s daty > 1 den od sebe = dva příspěvky. Builder deduplikuje znovu jako pojistku.

**Akceptace.** Opakovaný běh bez nových dat nepřidá žádný řádek.

## F8. Pohled „Podle kategorií“ a detail kanálu

**Účel.** Výchozí pohled – dlaždice po kategoriích. Dlaždice: náhled, odznak oficiálnosti, stav capture, poslední příspěvek s odkazem, datum, počet komentářů; u webů vydavatel SSL, platnost, „vyprší za N dní“ (≤14 červeně, ≤30 oranžově), online/odezva, uptime %.
Detail kanálu (`#details`): všechna metadata z F6, `hidden_links`, historie příspěvků kanálu, odkaz na komentáře, archivní odkazy.
Volba „skrýt screenshoty“; deep linky `card:<URL>` (např. ze Zprávy dne) a `#ev-<id>`.

## F9. Chronologie („Podle data aktualizace“)

Všechny dlaždice bez kategorií, seřazené podle data posledního příspěvku (nejnovější nahoře, bez data na konci). Sekce `#cat-chrono`.

## F10. Feed

Časová osa příspěvků z deduplikované historie + zajímavé komentáře se štítkem „komentář“. Filtr podle zdroje (shoduje se i s `urls[]`), zrušení filtru, prázdný stav „Žádné aktualizace pro zvolený filtr“. Sekce `#cat-feed`.

## F11. Histogram

Počet archivovaných příspěvků v čase (po dnech) z deduplikované historie, filtrovatelný podle kategorie. Sekce `#cat-histogram`; bez dat text „Žádná data pro histogram“.

## F12. Zajímavé komentáře s ověřováním faktů

**Účel.** Zachytit podstatné komentáře pod příspěvky oficiálních a souvisejících kanálů.

**Zdroje.** FB, IG, X, YT (`platform` ∈ `fb|ig|x|yt`); YT komentáře z veřejných dat stránky, ostatní z prohlížeče. Kandidáti → `data/comments_inbox/YYYY-MM-DD.json` → `add_interesting_comments.py --check`, pak zápis do `data/interesting_comments.jsonl`. Typicky 0–10 denně.

**Atributy.** `id`, `platform`, `channel`, `channel_url`, `post_url`, `post_title_or_excerpt`, `comment_url` (jen doklad v datech), `comment_text`, `author_type` (`official_account|public_figure|anon`), `author`, `comment_date_iso` (+ `comment_date_approx`), `first_seen`, `last_seen`, `reasons[]`, `reason_note`, `engagement{likes,replies}`, `screenshot` (`screenshots/comments/`), `source_method`, `anonymized`, volitelně `verification`.

**Slovník důvodů (`reasons`)**: insider info / informace zevnitř; kritika / stížnost; otázka s odpovědí úřadu; odpověď oficiálního účtu; dezinformace / propaganda; nová informace / fakt; výrazný ohlas; nábor / zkušenost z AZ; bezpečnostní riziko (OPSEC). Banální komentáře („super 👍“) se neukládají.

**Pravidla.**
- **Necenzurovat** – komentář se nevyřazuje kvůli nepodloženému tvrzení či obvinění.
- **Ověřovat jen** komentáře s obviněním nebo sporným faktickým tvrzením; ostatní nemají štítek.
- `verification = {status, checked_at (ISO, Praha), verifier: "Grok Bot", summary (1–2 věty česky), sources:[{title,url}]}`. Stavy: **potvrzeno** ✔, **částečně pravdivé** ◐, **vyvráceno** ✖, **nepodloženo / nelze ověřit** ?. První tři vyžadují aspoň jeden zdroj; do zdrojů jen skutečně načtené URL (přednostně mo.gov.cz, acr.mo.gov.cz, army.cz, pak seriózní média). Dodatečná ověření přes `data/verifications/YYYY-MM-DD.json` a `--verify`.
- **Soukromí**: `anon` se zobrazí jako „uživatel“, `author_name` se zahodí, @zmínky neoficiálních účtů se maskují (povolené jsou jen oficiální/veřejné handly). Odkaz vede na permalink příspěvku, ne na profil komentujícího. Jméno jen u oficiálních účtů, organizací a veřejně činných osob.
- Dedup: kanál + normalizovaný text (120 znaků); sloučení drží `first_seen`, aktualizuje `last_seen`, `engagement`, `reasons`.

**Výstup.** Sekce `#cat-komentare` (karty, štítky důvodů, filtr, rozbalitelné ověření: stav, čas ověření, „ověřil Grok Bot“, počet zdrojů, shrnutí, seznam zdrojů) + `interesting_comments.json`.

**Akceptace.** Validátor odmítne neznámý důvod, platformu, stav ověření nebo ověření bez zdroje; v publikovaných datech není jméno soukromé osoby.

## F13. Dostupnost webů a SSL (health check)

**Účel.** Denní monitoring webů. `check_sites_health.py` → `data/health_history.jsonl` (1 řádek / URL / den, týž den se přepíše).

**Atributy.** `date`, `ts`, `url`, `host`, `up`, `http_status`, `final_url`, `response_ms`, `error`, `tls{ok, version, issuer_o, issuer_cn, subject_cn, san_count, not_before, not_after, days_to_expiry, verify_error, error}`. Ověření certifikátu se nikdy nevypíná; TLS chyba se zapíše poctivě.

**Výstup.** Sekce `#cat-health`: řaditelná tabulka (výchozí podle dnů do expirace), denní pás dostupnosti, sparkline dnů do expirace, globální graf dostupnosti, uptime %.

## F14. Kontrola odkazů

`check_links.py` projde všechny odchozí odkazy hotového webu → `data/link_status.json` (`status` ∈ `ok|broken|login_wall|unknown`, `http_status`, `final_url`, `method`, `response_ms`, `error`, `social`, `sources`, `checked_at`) a `data/link_history.jsonl`. Sociální sítě vracející botům 400 = `login_wall`, ne `broken`. Na webu blok `#kontrola-odkazu`; nefunkční odkaz je přeškrtnutý s „⚠ nedostupný“.

## F15. Archivace (archive.org / archive.is) – volitelný krok

- `archive_lookup.py` (jen čtení) zjistí nejnovější snímek archive.org → `data/archive_status.json` (`wayback{status ∈ archived|none|error, snapshot_url, timestamp…}`, `archive_is{…}`, `wayback_browse`, `archive_is_browse`). U odkazů se zobrazí malé odkazy `archive.org` / `archive.is`.
- Ukládání (`archive_submit.py --cap 60`) je **poslední, nepovinný krok na pozadí**: ~12 s mezi požadavky, HTTP 429 = stop, log `archive_submit_log.jsonl`. Na výsledek se nečeká, projeví se při dalším buildu. Captcha se nikdy neřeší.

## F16. Stránka `archiv-is.html`

Samostatná pomocná stránka (`build_archive_is_page.py`, ~850 odkazů) pro ruční uložení odkazů na archive.is v prohlížeči uživatele (z serveru blokuje captcha). Generuje se po každém buildu, jinak by ji synchronizace smazala.

## F17. Události + export do kalendáře (.ics)

**Zdroje.** doarmady.mo.gov.cz (náborové akce, nástupní termíny), kalendář akcí MO, acr.mo.gov.cz (cvičení), weby KVV, unob.cz. Kandidáti → `data/events_inbox/YYYY-MM-DD.json` → `update_events.py --check` → `data/events.json`.

**Atributy.** `id` (hash názvu+data+místa), `title`, `type` (`přijímač/základní příprava|výběrové řízení/nábor|cvičení|veřejná akce|jiné`), `start`, `end` (včetně; `null` = neznámý), `all_day`, `date_precision` (`day|month`), `location`, `organizer`, `description`, `source_url`, `channel`, `confidence` (`potvrzeno|orientačně`), `first_seen`, `updated`, `replaced_ids[]` (posun termínu přes `replaces`).

**Pravidla.** Jen akce se zdrojem, který byl opravdu otevřen.

**Výstupy.** Sekce `#cat-events` (po měsících, „Právě probíhá“, filtr typu, sbalené „Proběhlé události“), pás „Nejbližší akce“ (3 nejbližší s konkrétním datem), u karty odkaz na zdroj, „＋ Google Kalendář“ a „⬇ .ics“. Soubory `events.json`, `events.ics` (celý feed, odběr `webcal://…/events.ics`), `events/<id>.ics`. ICS dle RFC 5545: `VTIMEZONE Europe/Prague`, stabilní `UID <id>@mapa-acr-az.vercel.app`, `DTSTAMP` = `updated`, celodenní akce `DTSTART;VALUE=DATE` s exkluzivním koncem (+1 den).

## F18. Zpráva dne (MOTD)

**Účel.** Denní shrnutí 3–6 skutečných změn.

**Postup.** `motd_facts.py` vypíše fakta za ~36 h (nové příspěvky, komentáře, změny up↔down, certifikáty ≤30 dní, TLS chyby, nové nefunkční odkazy, archivace, změny počtů vs. `motd/stats/`) → výběr položek → `set_motd.py --check` / zápis `data/motd/YYYY-MM-DD.json` (`--force` pro přepis dne).

**Atributy.** `date`, `generated_at`, `headline`, `items[]{text (max 2 věty), category ∈ příspěvek|komentář|dostupnost|SSL|odkazy|archiv|jiné, importance ∈ vysoká|střední, link (#cat-health|#kontrola-odkazu|#cat-komentare|#cat-feed|card:<URL kanálu>|https://…), source_url (permalink; varování, když je to účet)}`.

**Výstupy.** Box `#motd` nad dlaždicemi (položka `vysoká` = štítek „❗ důležité“), archiv „Starší zprávy dne“ `#cat-motd`, `motd.json`. Bez souboru se box nevykreslí.

## F19. Jiné platformy a evidence absence

`data/alt_platforms.json` (`platform`, `name`, `url`, `handle`, `officiality` – např. `absence`/`policy`, `notes`, `evidence`): eviduje alternativní platformy (WhatsApp, Discord…) a doložené absence (např. žádný oficiální Telegram AČR/MO/AZ). Sekce `#alt-platforms`.

## F20. Relativní časy na klientovi

Builder generuje jen absolutní časy: `<time class="reltime" data-iso="…" title="d. m. rrrr h:mm">`. Skript v prohlížeči dosadí relativní text při načtení, každých 60 s a při návratu na záložku; u expirací certifikátů přepočítá i barvu. Akceptace: ve výstupu nejsou serverem generované texty „před N …“ (mimo obsah příspěvků).

## F21. Build, publikace a ověření

- `build_site_v2.py` → `site_v2/` (smaže a vytvoří znovu; vše inline), pak `build_archive_is_page.py`. Výstupy: `index.html`, `updates_history.json`, `interesting_comments.json`, `motd.json`, `events.json`, `events.ics`, `events/`, `thumbs/`, `full/`, `screenshots/comments/` (jen odkazované).
- Publikace: synchronizace do git mirroru → push `main` → statický hosting (Vercel, bez `vercel.json`; `.ics` = `text/calendar`).
- Ověření živého webu: HTTP 200 a přítomnost markerů `cat-chrono`, `badge-oficialni`, `cat-komentare`, `cat-events`; `/events.ics` jako `text/calendar`.
- Do gitu nikdy: cookies, profily prohlížeče, tokeny, `.env`, nerozmazané snímky, surové capture.

## F22. Pořadí denního běhu (shrnutí)

1 záloha screenshotů → 2 capture kanálů + rozmazání → 3 RSS → 4 enrichment → 5 X → 6 health → 7 komentáře + ověření → 8 události → 9 build (historie) → 10 kontrola odkazů → 11 archive lookup → 12 Zpráva dne → 13 finální build + archiv-is.html → 14 publikace → 15 ověření živého webu → 16 shrnutí správci → 17 (nepovinně, na pozadí) archivace.

---

## Souhrnná akceptační kritéria

1. Stránka obsahuje pohledy: kategorie, chronologie, feed, histogram, komentáře, dostupnost a SSL (+ kontrola odkazů), události; box Zpráva dne a pás Nejbližší akce.
2. Každý kanál má kategorii (F2) a odznak Oficiální/Neoficiální (F3).
3. Každá zobrazená informace má zdroj; odkazy na příspěvky jsou permalinky, kde existují.
4. Historie i komentáře jsou deduplikované; opakovaný běh nic nezduplikuje.
5. Ověřené komentáře mají stav, čas, ověřovatele „Grok Bot“ a (u potvrzeno/částečně/vyvráceno) načtené zdroje; neověřují se běžné názory.
6. Žádné jméno soukromého komentujícího, žádná identita procházejícího účtu na snímcích, žádné tajné údaje v repozitáři.
7. `events.ics` je validní RFC 5545 se stabilními UID; každá událost má `source_url`.
8. Časy jsou absolutní v datech, relativní jen v prohlížeči.
9. Archivace nikdy neblokuje publikaci.
