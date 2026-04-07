---
description: "Konwencje Python dla AutoResearch_SQLServer — benchmarking wariantów SQL, konfiguracja połączenia, generowanie wariantów zapytań."
applyTo: "**"
---

# AutoResearch_SQLServer

Narzędzie CLI w Pythonie do automatycznego benchmarkowania wariantów zapytań SQL na Microsoft SQL Server. Przyjmuje bazowe zapytanie SQL, generuje warianty strukturalne (JOIN→EXISTS, TOP N, NOLOCK, RECOMPILE), wykonuje każdy na żywej bazie danych i raportuje najszybszy.

## Stos technologiczny

- Python 3.10+
- `pyodbc` — połączenie z SQL Server przez ODBC
- ODBC Driver 17 for SQL Server
- Konfiguracja połączenia w `db.py` (connection string)
- NIE używaj ORM — surowe zapytania SQL przez `pyodbc`

## Środowisko wirtualne (KRYTYCZNE)

**KAŻDA komenda Pythona MUSI być uruchamiana w aktywowanym środowisku `.venv`.** Dotyczy to uruchamiania skryptów, instalacji pakietów (`pip`) i wszelkich operacji Pythona.

Przed KAŻDYM uruchomieniem komendy w terminalu:

```powershell
.\.venv\Scripts\Activate.ps1
```

- NIE uruchamiaj `python`, `pip` ani żadnych skryptów na interpreterze systemowym
- Jeśli otwierasz nowy terminal — ZAWSZE aktywuj venv jako pierwszy krok

## Architektura

```
main.py (orkiestracja)
  ├── query.sql          → bazowe zapytanie SQL
  ├── variants.py        → generowanie wariantów
  ├── runner.py          → wykonanie i pomiar czasu
  ├── db.py              → połączenie z SQL Server
  └── results.json       → wyniki benchmarku (generowane)
```

- `main.py` — orkiestrator: ładuje zapytanie z `query.sql`, generuje warianty, uruchamia benchmark, zapisuje wyniki do `results.json`
- `query.sql` — bazowe zapytanie SQL do optymalizacji
- `variants.py` — generuje warianty strukturalne przez transformacje stringowe w funkcji `generate_variants()`
- `runner.py` — wykonuje zapytania na SQL Server i mierzy czas wall-clock w `run_query()`
- `db.py` — fabryka połączeń z SQL Server (`get_connection()`) przez `pyodbc`
- `results.json` — wynik benchmarku w formacie JSON (generowany automatycznie)

## Konwencje kodowania

- Zmienne i funkcje: `snake_case`
- Pliki: `snake_case.py`
- Wyniki benchmarku zapisywane do `results.json` jako JSON z `indent=2`
- Connection string w `db.py` — NIGDY nie commituj prawdziwych credentials do repozytorium
- Nowe warianty zapytań dodawaj w `generate_variants()` w `variants.py`

## Czego NIE robić

- NIE commituj prawdziwych haseł ani connection stringów do repozytorium
- NIE używaj ORM (SQLAlchemy, Django ORM) — projekt celowo używa surowego SQL przez `pyodbc`
- NIE dodawaj zależności bez uzasadnienia — architektura jest celowo minimalna
- NIE uruchamiaj Pythona poza środowiskiem `.venv`

## Komendy

```bash
# ZAWSZE najpierw aktywuj venv
.\.venv\Scripts\Activate.ps1

# Uruchomienie benchmarku
python main.py

# Instalacja zależności
pip install -r requirements.txt
```

## Commit Convention

- Opis commita ZAWSZE w języku angielskim
- Format: krótki, imperatywny opis (np. `Add variant for CTE rewrite`, `Fix connection timeout handling`)
- Nie używaj prefiksów typu `feat:`, `fix:` — prostota ponad konwencje

## Przed commitem

Sprawdź, czy nie trzeba zaktualizować:
- `README.md` — jeśli zmiana wpływa na dokumentację użytkownika (nowe warianty, zmiana konfiguracji)
- `CHANGELOG.md` — dodaj wpis w sekcji `[Unreleased]` opisujący zmianę (format Keep a Changelog)
