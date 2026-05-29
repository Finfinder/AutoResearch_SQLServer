---
# Synchronizacja metadanych GitHub

Pliki `.github/gh-sync.json` oraz `.github/issue-seed.json` (jeśli występują) są źródłem prawdy dla metadanych repozytorium i seedowania issue. Sekwencja działania: `sync` → `seed`.

Przykładowe komendy PowerShell (dry-run):

```powershell
.\scripts\sync-github-meta.ps1 -DryRun > sync-dry-run.log
.\scripts\seed-github-issues.ps1 -DryRun >> sync-dry-run.log
```

Przykładowe komendy PowerShell (Apply):

```powershell
.\scripts\sync-github-meta.ps1 -Apply
.\scripts\seed-github-issues.ps1 -Apply
```

Backup przed Apply:
- Wykonaj backup przez GitHub REST API (np. `gh api` lub `curl`) i zapisz wynik jako JSON.
- Sprawdź kodowanie plików (UTF-8).

Checklist przed Apply:
- Backup REST
- Walidacja UTF-8
- Unikalność `sourceId`
- Sprawdź format tytułów issue (np. `AR-\d+` lub inny repo-prefix)

Jeśli brak lokalnych skryptów `scripts/sync-github-meta.ps1` / `scripts/seed-github-issues.ps1` — dostosuj skrypty lub zignoruj workflow. Workflow ma zachowanie przyjazne (nie failuje jeśli skrypty nie istnieją).

Dostosuj prefix issue (np. TB-, IA-, AR-, SEQ-, DQN-) jeśli używasz lokalnego seed.

Króciutka checklista walidacji:
- UTF-8
- `sourceId` unikatowe
- Tytuły zgodne z konwencją prefixów

---
