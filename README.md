# YARA Report System

Sistema de relatório fotográfico de manutenção preventiva. Backend Python + SQLite, frontend HTML puro.

## Primeiro deploy no Coolify

### 1. Configurar Storage ANTES do deploy

**Obrigatório.** Se pular este passo, todos os dados (banco + imagens) serão perdidos no primeiro redeploy.

No Coolify, dentro do serviço, vá em **Storages → + Add**:

#### Volume Mount (recomendado)

O Docker gerencia o volume, não precisa criar pasta no servidor:

| Campo | Valor |
|---|---|
| Name | `yara_data` |
| Destination Path | `/app/data` |

*(Source Path: deixar vazio)*

#### Bind Mount (alternativo)

Se quiser acessar os arquivos direto pelo servidor em `/data/yara`:

| Campo | Valor |
|---|---|
| Name | `yara_data` |
| Source Path | `/data/yara` |
| Destination Path | `/app/data` |

*(Antes do deploy, criar a pasta no servidor: `mkdir -p /data/yara/images && touch /data/yara/database.db && chmod -R 777 /data/yara`)*

### 2. Fazer deploy

Clicar **Deploy**. Na primeira subida o entrypoint detecta que o volume está vazio e copia automaticamente:

- Banco de dados (`database.db`) com 72 equipamentos e relatórios
- 77 imagens em `data/images/`

### 3. Usar normalmente

Salvar relatórios, adicionar equipamentos, etc.

## Redeploy / Force Redeploy

**Seguro** se o storage foi configurado no passo 1. O volume persiste entre deploys. O entrypoint detecta que já existe dado e não sobrescreve nada.

## Estrutura

```
/app/
├── server.py          # backend Python (porta 8880)
├── schema.sql         # schema SQLite
├── index.html         # frontend
├── entrypoint.sh      # script de inicialização (seed automático)
├── tools/             # scripts auxiliares
└── data/              # volume persistente
    ├── database.db    # banco SQLite
    └── images/        # fotos dos relatórios
```

## Porta

8880

## ATENÇÃO

- Sempre configure o Storage do Coolify antes do primeiro deploy.
- Nunca use `docker compose down -v` no servidor (apaga o volume).
- Force Redeploy no Coolify **não** apaga o volume se ele foi configurado via Storages.