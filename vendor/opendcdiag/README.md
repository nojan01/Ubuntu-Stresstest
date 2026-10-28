# vendor/opendcdiag

[OpenDCDiag](https://github.com/opendcdiag/opendcdiag) (Intel, Apache 2.0) sucht
stille Datenfehler (*silent data corruption*) je CPU-Kern.

| Datei                 | Beschreibung                                         |
|-----------------------|------------------------------------------------------|
| `opendcdiag`          | Build mit `-march=haswell` (AVX2)                    |
| `opendcdiag-generic`  | Build mit `-march=x86-64-v2` für CPUs ohne AVX2      |
| `LICENSE`             | Apache License 2.0                                   |
| `LICENSE.3rdparty`    | Lizenzen der eingebundenen Fremdkomponenten          |
| `COMMIT`              | Upstream-Commit des Builds                           |

Die Dateien stehen nicht im Git-Repo. Sie werden reproduzierbar gebaut mit:

```bash
bash scripts/build_opendcdiag.sh
```

`scripts/build_portable_bundle.sh` (und damit `.deb` und AppImage) ruft das
Skript automatisch auf, wenn die Binaries fehlen. Laufzeitabhängigkeiten:
`libzstd1`, `zlib1g`, glibc ≥ 2.39.
