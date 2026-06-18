# YARA Report System

Sistema novo em Python + SQLite para relatórios fotográficos.

Este projeto **não muda a interface nem o modelo de impressão**. O servidor novo entrega o `index.html` preservado no próprio projeto e mantém as rotas antigas (`/api/report`, `/api/reports`) para a página continuar funcionando no mesmo padrão visual.

## Rodar

```bash
python server.py
```

Abra `http://127.0.0.1:8890`.

## Importar JSONs antigos

```bash
python tools/import_old_reports.py "../data/reports" --old-data-dir "../data"
```

## Como as imagens são salvas

- O banco guarda apenas caminho e hash.
- As imagens ficam em `data/images/YYYY-MM-DD/`.
- SHA256 evita salvar imagem exatamente igual mais de uma vez.
- JSON antigo com base64 é aceito pelo importador e convertido para arquivo real.
- Ao carregar o relatório, a página recebe URLs leves (`/images/...`) em vez de base64 gigante.
