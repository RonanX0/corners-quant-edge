# CORNERS QUANT EDGE — Vercel Edition 3.1

Aplicação FastAPI + interface HTML/CSS/JavaScript para analisar probabilidades de escanteios Over/Under, validar modelos cronologicamente e explorar odds de valor esperado.

## Pronto para Vercel

O projeto foi adaptado para as **Vercel Python Functions**. O `app.py` na raiz é um entrypoint detectado automaticamente. Os arquivos de interface estão em `public/`, que é distribuído pela CDN da Vercel.

**IMPORTANTE:** A implantação na conta Vercel ainda precisa ser realizada por você (login e autorização). Este pacote é para publicação; o link `.vercel.app` só existirá depois do deploy.

### Método A — Windows, sem GitHub

1. Instale o [Node.js LTS](https://nodejs.org/) se ainda não tiver. Feche e reabra o terminal para reconhecer o `npx`.
2. Extraia este ZIP e abra a pasta extraída **CORNERS_QUANT_EDGE_VERCEL**.
3. Clique duas vezes em `PUBLICAR_VERCEL_WINDOWS.bat`.
4. Faça login no Vercel quando solicitado e confirme as perguntas de configuração do projeto. Se necessário, escolha criar um novo projeto, pasta raiz atual (`.`) e deixe comandos de build em branco.
5. A Vercel mostrará a URL HTTPS ao terminar (p. ex. `https://nome-do-projeto.vercel.app`).

### Método B — GitHub + deploy automático

1. Crie um repositório GitHub (preferencialmente privado) com o conteúdo desta pasta na **raiz** do repositório.
2. Em https://vercel.com/new, selecione **Import Git Repository** e conecte o repositório.
3. Confirme a detecção do framework **FastAPI / Python**, diretório raiz `./`. Não configure `npm run build`, `dist`, ou Next.js.
4. Clique em Deploy. Mudanças posteriores na branch configurada geram novos deploys.

### Método C — CLI em qualquer sistema

```bash
npx vercel@latest login
npx vercel@latest --prod
```

Execute os comandos na pasta que contém `app.py`, `vercel.json`, `requirements.txt` e `public/`.

## Rotas e arquivos

| Arquivo / rota | Função |
|---|---|
| `app.py` | FastAPI stateless e validação de CSVs |
| `engine.py` | modelos estatísticos, previsões, backtest e avaliação de odds |
| `public/index.html` | dashboard |
| `public/static/app.js` | controle da interface, importação e exportação |
| `public/static/style.css` | layout responsivo |
| `data/historico_452_jogos.csv` | demonstração histórica, **sem odds** |
| `GET /api/health` | diagnóstico do servidor |
| `POST /api/query` | cálculo de visão geral, equipes, projeção, backtest e ROI |
| `POST /api/validate/matches` | validação dos resultados importados |
| `POST /api/validate/odds` | validação das odds históricas |
| `POST /api/import/premier10` | opcional, busca dez temporadas de fonte pública a pedido do visitante |

### Por que as importações foram alteradas?

As Vercel Functions têm sistema de arquivos somente leitura, com `/tmp` temporário. No aplicativo local antigo, todos os usuários poderiam compartilhar arquivos `data/user_matches.csv` e `data/user_odds.csv`, o que não funciona de forma confiável na Vercel e criaria risco de dados cruzados. A versão 3.1 remove os endpoints de escrita compartilhada.

O histórico e as odds enviados são validados e armazenados **no armazenamento local do navegador (localStorage)**. Quando necessário para cálculos, o navegador transmite os CSVs à API via HTTPS; a API processa em memória e não grava permanentemente. Isso significa:
- os dados importados ficam disponíveis naquele navegador e origem enquanto não forem apagados, sujeitos às configurações do browser;
- outros dispositivos/navegadores não compartilham esses arquivos;
- limpar dados do site apaga os CSVs locais;
- quem administrar uma instalação pública deve configurar proteção de acesso e limites de tráfego para custos e abuso.

Para persistência entre dispositivos e contas, precisaremos integrar banco de dados e autenticação, como Vercel Postgres/Neon e um sistema de login. Não há contas nesta versão.

### Limites e metodologia

- Importação: até 1 MB de resultados e 300 KB de odds; formato CSV UTF-8.
- A base inicial contém resultados de 452 partidas em cinco ligas (2026/27), sem odds.
- Os modelos suportados estão em `engine.py`: variantes de médias regularizadas, Poisson e regressão binomial negativa.
- O histórico de treino e validação é separado cronologicamente para evitar utilizar resultados futuros.
- O painel mostra estimativas e um cenário conservador **ilustrativo**, não odds garantidas.
- Sem preços de escanteios registrados antes das partidas, **não existe ROI histórico validado**.
- Os CSVs de odds enviados por usuários não são autenticados externamente.
- `POST /api/import/premier10` exige internet e depende de disponibilidade e condições de uso da fonte externa. Há um limite de 3 importações por IP a cada 10 minutos, mas ele vale só por instância da função; numa instalação pública, crie também uma regra de rate limit no Vercel Firewall para essa rota.
- Datas: `AAAA-MM-DD` é lido como ano-mês-dia; `DD/MM/AAAA` (padrão football-data) é lido com o dia primeiro. Arquivos que misturam os dois formatos são recusados.
- ROI: quando um jogo tem várias cotações válidas, usa-se só a observação mais recente antes do início, escolhida antes de olhar o EV (evita escolher a melhor casa em retrospecto).
- O sistema não realiza apostas nem oferece garantias de lucro.

### Rodar e testar localmente

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8765
```

Abra http://localhost:8765. Teste `GET /api/health` e `POST /api/query`.

Para executar os testes automatizados:

```bash
python -m unittest discover -s tests -v
```

## Arquitetura

Frontend hospedado no CDN da Vercel → FastAPI (Vercel Function) → engine pandas/numpy/scipy, usando amostra somente leitura ou CSV carregado na requisição. Não há serviço em segundo plano, scraping ao vivo nem execução de apostas.

### Fontes

- Vercel FastAPI: https://vercel.com/docs/frameworks/backend/fastapi
- Vercel Functions filesystem: https://vercel.com/docs/functions/runtimes
- Amostra de jogos: https://github.com/oladeeayo/open-matchday
- Importação opcional de Premier League: https://github.com/datasets/football-datasets

Confirme os termos de licença e utilização antes de publicar dados de terceiros.
