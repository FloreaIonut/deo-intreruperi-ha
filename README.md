# Întreruperi Distribuție Oltenia (`deo_intreruperi`)

Integrare custom pentru Home Assistant (compatibilă HACS), versiunea 0.2.0. Citește programul săptămânal (PDF) de întreruperi planificate publicat de Distribuție Oltenia pentru un județ (implicit Argeș), caută cuvinte-cheie, străzi sau posturi PTA și te alertează când apar.

> Proiect neoficial, neafiliat Distribuție Oltenia. Datele vin din documentele publice de pe site-ul lor.

## Ce face
- La fiecare 6 ore descarcă pagina județului, găsește linkurile PDF ale săptămânii curente și ale celor viitoare (max 3) și extrage textul cu `pypdf`.
- Caută cuvintele-cheie (implicit `cuza voda`, fără diacritice și indiferent de scriere: „Str. Cuza-Vodă”) și numele de posturi PTA pe care le urmărești.
- **Filtru pe localitate:** o potrivire pe cuvânt-cheie contează doar dacă blocul conține localitatea urmărită (implicit `curtea de arges`). Același nume de stradă din altă localitate rămâne în istoric, dar nu alertează. Dacă localitatea nu poate fi determinată, potrivirea e tratată ca relevantă (mai bine un fals pozitiv decât una ratată).
- **Istoric persistent:** potrivirile (relevante și nu) se salvează în `Store`; la prima rulare un task în fundal procesează tăcut până la 26 de săptămâni trecute (fără evenimente).
- La fiecare potrivire nouă relevantă emite evenimentul `deo_intreruperi_potrivire`; cheile notificate se păstrează și după repornire, deci nu se repetă alerta.

## Instalare
**HACS (repository custom):** HACS > meniul cu trei puncte > Custom repositories > adaugă `https://github.com/FloreaIonut/deo-intreruperi-ha`, tip *Integration* > instalează > repornește Home Assistant.

**Manual:** copiază `custom_components/deo_intreruperi/` în `/config/custom_components/` și repornește Home Assistant. `pypdf` se instalează automat.

## Configurare
Setări > Dispozitive și servicii > Adaugă integrare > „Întreruperi Distribuție Oltenia”. Câmpurile se pot modifica ulterior din Opțiuni.

| Câmp | Implicit | Rol |
|---|---|---|
| Adresa paginii județului | pagina Argeș | sursa PDF-urilor (poți pune pagina altui județ DEO) |
| Cuvinte-cheie | `cuza voda` | listă separată prin virgulă; declanșează alerta |
| Localitate de urmărit | `curtea de arges` | filtrează potrivirile pe cuvinte-cheie + senzorul „Rânduri localitate” |
| Nume PTA / posturi | gol | listă separată prin virgulă; declanșează alerta (mereu relevante) |

Erori la configurare: `cannot_connect`, `blocked` (WAF/403/pagină „Blocked”), `no_links`.

## Entități (dispozitivul „Întreruperi Distribuție Oltenia”)
| Entitate | entity_id |
|---|---|
| Întrerupere detectată (on = potrivire azi/viitor) | `binary_sensor.intreruperi_distributie_oltenia_intrerupere_detectata` |
| Potriviri (număr; atribut `potriviri`) | `sensor.intreruperi_distributie_oltenia_potriviri` |
| Ultima săptămână procesată (+ `data_verificarii`, `saptamani`, `erori`) | `sensor.intreruperi_distributie_oltenia_ultima_saptamana_procesata` |
| Rânduri localitate (informativ) | `sensor.intreruperi_distributie_oltenia_randuri_localitate` |
| Următoarea întrerupere (timestamp + atribute) | `sensor.intreruperi_distributie_oltenia_urmatoarea_intrerupere` |
| Ultima întrerupere relevantă cu data <= azi | `sensor.intreruperi_distributie_oltenia_ultima_intrerupere` |
| Istoric întreruperi (stare = nr. relevante; atribute `istoric`, `istoric_toate`, `backfill_ramas`) | `sensor.intreruperi_distributie_oltenia_istoric_intreruperi` |

Dacă site-ul blochează cererea sau nu se poate citi nicio săptămână, entitățile devin `unavailable`, iar logul conține motivul. Senzorii de istoric rămân disponibili (citesc din `Store`).

## Eveniment `deo_intreruperi_potrivire`
Date: `saptamana`, `data` (AAAA-LL-ZZ), `interval_orar`, `localitate`, `zona`, `text`, `cuvant`, `tip` (`cuvant` / `pta`), `relevant`, `localitate_necunoscuta`, `entry_id`.

## Automatizări exemplu
Fișierul [`automatizari.yaml`](automatizari.yaml) conține două automatizări (sintaxa `triggers:` / `trigger:`, HA 2024.10+):
1. **Alertă** la fiecare eveniment `deo_intreruperi_potrivire`, cu data, intervalul, localitatea și zona.
2. **Verificare săptămânală** (luni 08:00): rezumatul săptămânii sau un avertisment dacă citirea a eșuat.

Înlocuiește `notify.mobile_app_TELEFONUL_TAU` cu serviciul de notificare al telefonului tău. Exemplu minim:

```yaml
triggers:
  - trigger: event
    event_type: deo_intreruperi_potrivire
actions:
  - action: notify.mobile_app_TELEFONUL_TAU
    data:
      title: "Întrerupere curent"
      message: "{{ trigger.event.data.data }}, {{ trigger.event.data.interval_orar }}: {{ trigger.event.data.zona | truncate(200) }}"
```

## Card de istoric
[`card-istoric.yaml`](card-istoric.yaml) este un card `markdown` (core, fără dependențe) cu „Ultima întrerupere” și tabelul din atributul `istoric`. Dashboard > Editează > Adaugă card > Manual, apoi lipește conținutul.

## Limite
- **Neoficial:** nu e afiliat Distribuție Oltenia; dacă schimbă formatul paginii sau al PDF-urilor, integrarea poate înceta să funcționeze.
- **WAF:** site-ul poate răspunde cu 302/403 către o pagină „Blocked”. Integrarea folosește anteturi de browser și reîncearcă, dar blocarea depinde de IP-ul tău.
- **PDF scanat/imagine:** fără OCR; se raportează „PDF fără text”.
- **Posturile PTA nu sunt străzi:** programul listează mai ales posturi (PTA/PTCZ), nu străzi. „Cuza Vodă” apare doar dacă DEO scrie strada. Trebuie să adaugi **PTA-ul tău** la „Nume PTA / posturi”. Îl găsești pe harta GIS a Distribuție Oltenia (caută adresa ta și notează codul/numele postului care te alimentează).
- Împărțirea localitate/zonă este euristică; uneori localitatea apare goală (potrivirea rămâne relevantă, marcată `localitate_necunoscuta`).
- Fișierele se pot modifica la același URL; se re-verifică la 6 ore.

## Depanare
Adaugă în `configuration.yaml`:
```yaml
logger:
  logs:
    custom_components.deo_intreruperi: debug
```
Apoi uită-te în Setări > Sistem > Jurnale după `custom_components.deo_intreruperi`.

## Teste
```
python3 -m venv v && v/bin/pip install pypdf pytest-homeassistant-custom-component
v/bin/python -m pytest
v/bin/python -I tests/test_api_http.py
```
Fixturile din `tests/fixtures/` sunt texte sintetice sau extrase mici. Testul cu PDF-uri reale se rulează doar dacă `DEO_PDF_DIR` indică un dosar cu `w1.pdf` (26.10.2026) și `w2.pdf` (19.10.2026); altfel este sărit.

---

## English summary
Unofficial Home Assistant custom integration (HACS-compatible) that reads the weekly planned-outage PDF published by Distribuție Oltenia (Romanian electricity distributor) for a county (default: Argeș), searches it for keywords, street names and transformer-station (PTA) names, filters by locality, keeps a persistent history, fires the `deo_intreruperi_potrivire` event on a new relevant match and exposes 6 sensors plus 1 binary sensor.

**Install:** HACS > Custom repositories > `https://github.com/FloreaIonut/deo-intreruperi-ha` (type Integration), or copy `custom_components/deo_intreruperi/` to `/config/custom_components/`. Restart, then add the integration from Settings > Devices & services.

**Limits:** not affiliated with Distribuție Oltenia; the website may block requests (WAF); scanned PDFs are not supported (no OCR); the schedule lists mostly PTA stations rather than streets, so add your own PTA name (see the operator's GIS map). Debug with logger `custom_components.deo_intreruperi`. Examples: `automatizari.yaml` (replace `notify.mobile_app_TELEFONUL_TAU`), `card-istoric.yaml`.

License: MIT.
