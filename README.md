# YARA Report System

Sistema de relatório fotográfico com frontend HTML, backend Python e SQLite.

O `index.html`, o CSS, o layout A4 e a impressão são preservados. Os dados reais ficam somente no storage persistente do servidor.

## Coolify

Antes do deploy, adicione em **Storages** um Volume Mount:

| Campo | Valor |
|---|---|
| Name | `yara_data` |
| Destination Path | `/app/data` |

Deixe o Source Path vazio. Como alternativa, use um Bind Mount apontando uma pasta permanente para `/app/data`.

## Começar do zero

1. Guarde qualquer backup que ainda seja necessário.
2. Apague o serviço antigo.
3. Apague também o volume/storage antigo.
4. Crie o serviço novamente.
5. Configure o storage em `/app/data` antes do primeiro deploy.
6. Faça o deploy.

O sistema cria automaticamente:

```text
/app/data/
├── database.db
├── images/
└── snapshots/
```

O repositório não contém mais banco preenchido, relatórios ou fotos reais. A lista padrão existente na página é enviada ao banco na primeira abertura; relatórios e fotos começam vazios.

## Persistência

Com `/app/data` configurado como storage persistente, redeploy e reinício não apagam o banco nem as imagens. Remover o volume/storage é a ação que apaga os dados.

## Importação

- **Mesclar** mantém dias que não existem no arquivo e atualiza apenas os dias importados.
- **Substituir** cria um snapshot e troca a base pelo backup.
- a importação é validada antes de ser confirmada;
- placeholders como `[IMAGE_REMOVED]` não contam como fotos;
- ocorrências repetidas são mantidas;
- MD400 e MD410 podem usar o mesmo IP como atendimentos separados.

## Contagem mensal

A capa mensal usa apenas dados confirmados pelo servidor e mostra dias registrados, atendimentos e IPs únicos atendidos em relação à base cadastrada.

## Rodar localmente

```bash
python server.py
```

Abra `http://127.0.0.1:8880`.
