> **Independent student research project.** Red-and-white visual styling inspired by HSBC; not affiliated with or endorsed by HSBC.

# EGB & Inflation | Desk Brief

A **compact Python/Streamlit research dashboard** illustrating how a junior analyst could support a European Government Bonds & Inflation desk: curve moves, sovereign relative value, inflation, and daily position risk.

## Structure — deliberately short

- **Home / Market Brief:** a concise market snapshot, desk scope and a shared hypothetical PV01/P&L monitor.
- **01 Core EGB, 2–10Y:** German Bund and French TEC points, slopes, reference spreads and a compact curve-pricing toolkit. Desk focus: Hugo Fuentes.
- **02 Southern EGB, 3–15Y:** Italy, Spain and Portugal. Sovereign relative value, small downloadable market note, bond/asset-swap calculator. Desk focus: Vincenzo Asaro.
- **03 Inflation Trading:** Eurostat HICP, indexation, nominal/real breakevens and ZC inflation swap settlement. Desk focus: Hugo Guinamant and William Fortier.
- **04 Semi-Core & Less Liquid EGB, 2–10Y:** selected euro sovereign yields/spreads, simple hypothetical inventory risk. Desk focus: Ouassim Loussaief.

**No HSBC affiliation, proprietary position data or trading execution is implied.** The public professional descriptions motivated the themes, but the code is an independent personal project.

## Installation (Windows)

1. Extract the ZIP and open this folder in VS Code.
2. Double-click `START_WINDOWS.bat`, or run:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Market data is requested automatically when you open the relevant page. An API error is shown under **Data sources & download status**, without synthetic fallback. No CSV needs to be prepared for providers with working public feeds.

## Market data: actual behavior and limitations

| Country/topic | Automatic feed | Frequency and definition | Limitations |
| --- | --- | --- | --- |
| Germany | [Bundesbank BBSSY](https://www.bundesbank.de/en/statistics/money-and-capital-markets/interest-rates-and-yields/daily-yields-of-current-federal-securities-772220) | Daily current federal benchmark yields, 2/5/7/10/15/20/30Y | Original-maturity benchmark, subject to benchmark rolls; **not** a fitted zero curve |
| France | [Banque de France Webstat public selection](https://webstat.banque-france.fr/export/csv-columns/fr/selection/5385693) | Daily CNO-TEC constant-maturity yields, **all TEC columns actually found in the export** | Selection content and accessibility cannot be verified from this offline development runtime; rights/reuse restrictions apply; no TEC values shipped |
| Spain | [Banco de España TI 1.3](https://www.bde.es/webbe/es/estadisticas/compartido/datos/csv/ti_1_3.csv) | Daily government bond reference yields 3/5/10/15Y, per provider column aliases | Official CSV layout can change; parsing failure is reported, not hidden |
| Portugal | [BPstat API](https://bpstat.bportugal.pt/data/docs/) | BPstat official series 3Y, 5Y, 10Y, **mixed daily/monthly** | Series metadata is checked; monthly points are never used for daily spreads |
| Italy | [Banca d'Italia BMK0200](https://statistiche.bancaditalia.it/) | Published daily BTP benchmark yields | No directly callable tested public download URL; **optional CSV fallback**. ECB monthly 10Y always separately available |
| Southern / semi-core | [ECB IRS](https://data.ecb.europa.eu/data/datasets/IRS) | **Monthly** convergence-country 10Y average yields; DE/FR/IT/ES/PT and available BE/SI/SK/NL/FI/AT/IE/DK | Not daily cash quotes, and cannot produce a 2s10s/5s15s by itself |
| Inflation | [Eurostat prc_hicp_minr](https://ec.europa.eu/eurostat/web/hicp/information-data) | Monthly HICP annual rate `RCH_A`, `coicop18=TOTAL` | Realized HICP is not a market inflation forward |
| Z-spread zero curve | [ECB YC daily AAA zero spot](https://data.ecb.europa.eu/methodology/yield-curves) | Euro-area AAA **sovereign** continuous zero curve | Not €STR OIS; used only as clearly identified benchmark against user-supplied bond price |

### The Webstat CSV fix

The user-supplied Webstat selection contains **several TEC columns** (at least 1Y, 10Y, 15Y in a screenshot), despite earlier versions incorrectly assuming it was TEC10-only. `parse_webstat_multitenor()` detects all `FRMOYTEC<n>` metadata columns, parses French decimal commas and daily dates, and ingests precisely the valid observations present. **It never manufactures unprovided TEC maturities.** If the endpoint is blocked, the interface displays the issue. Download the CSV manually only as a fallback for permitted private use.

### How risk is estimated

- Any position is hypothetical and user-defined. Nominal government bond **model PV01** is derived from an annual-coupon *par-bond proxy* calibrated to the observed reference yield — **not the instrument-specific PV01 of a particular ISIN**.
- Signed PV01 = position price change under a +1bp yield shock. Long nominal bonds negative, short positive.
- First-order estimated P&L = signed PV01 × change in corresponding **observed reference yield** in bps.
- Daily and monthly P&L are displayed separately. No realized bank P&L, bid/ask, repo funding, actual client RFQs or live asset swap quotes are invented.

### Pricing tools (optional, collapsed)

- Annual **par-rate bootstrapping** with fit-to-par sanity checks.
- Model IRS (payer/receiver) and idealized OIS, with clearly user-supplied annual-rate assumptions. The OIS is **not** full compounded €STR pricing.
- Bond pricing and **Z-spread** with observed ECB AAA zero curve and a **user-entered** bond price.
- Simplified **par asset swap** cash-flow package using **user-entered curves** (not bond yield minus swap rate).
- Simple **zero-coupon inflation swap** maturity payoff and inflation indexation ratio. Not a production linker or forward inflation swap pricer.

## Run local tests

```powershell
python -m pytest -q
```

Tests cover the multi-column Webstat format, SDMX semicolon CSV, common-date slope/spread calculations, PV01 signs, Z-spread price reconstruction, par swap and ASW package consistency, inflation payoff and missing-data behavior.

**Tests use small simulated inputs only to verify mathematical logic**; no synthetic market history is displayed or distributed. Python execution in this development environment cannot reach the external statistical APIs, so the actual live endpoints are not confirmed operational until run on a network-enabled PC.

## Sources and publishing caution

Some historical reference series, notably French CNO-TEC / Euronext products, have reuse restrictions. This independent project fetches observations into the user's session, does not ship proprietary historical datasets, and does not guarantee redistribution rights. Verify the relevant provider terms before making a live public dashboard. Data provenance and publication dates remain visible in the application.
