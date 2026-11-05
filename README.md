# CORNERS QUANT EDGE v4

Plataforma de análise quantitativa de mercados **Over/Under de escanteios pré-jogo**: encontra jogos, estima a distribuição de escanteios, compara com odds reais identificadas por fonte e horário, classifica mercados com base em evidência e incerteza, valida modelos em walk-forward e mantém um registro de apostas.

> Ferramenta de pesquisa. Probabilidades são estimativas de modelo; não há promessa de lucro ou precisão. O sistema não executa apostas.

## O que funciona hoje

| Área | Situação | Observação |
|---|---|---|
| Painel | ✅ | Só mostra números calculados a partir de dados reais; avisa quando faltam dados. |
| Histórico real | ✅ sem chave | football-data.co.uk: escanteios, finalizações, no alvo, xG (temporadas recentes), árbitro. |
| Calendário real | ✅ sem chave | `fixtures.csv` semanal do football-data.co.uk (publicado às sextas e terças) + CSV próprio. |
| Odds de escanteios ao vivo | ⚠️ precisa de chave | The Odds API, mercado `alternate_totals_corners`. Sem `THE_ODDS_API_KEY` a fonte aparece como **desconectada** e nada é simulado. |
| Odds por CSV | ✅ | Com `observed_at`, `kickoff_at` e, para CLV, odds de fechamento. |
| Radar de Oportunidades | ✅ | Filtros por data, campeonato, mercado, linha, odd mín./máx., modelo, EV, classe; ordenação; análise; acompanhar; registrar aposta. |
| Análise de confronto | ✅ | Distribuição 0–20+, intervalos preditivos, linhas 7,5–12,5, odds justas, fatores do modelo, perfis das equipes, H2H, riscos. |
| Backtest científico | ✅ | Walk-forward com re-treino por bloco, validação × teste, Brier, log loss, NLL, MAE, acerto, IC por bootstrap de datas, por liga/temporada/linha, calibração. ROI/drawdown/CLV só com odds importadas. |
| Gestão de apostas | ✅ | Registro manual, banca, ROI, drawdown, gráficos, CSV. No navegador ou no PostgreSQL. |
| Banco de dados | ⚠️ precisa de configuração | PostgreSQL via `DATABASE_URL`; sem ela, tudo privado fica no navegador. |

## Modelos

Todos estimam a **distribuição completa** de escanteios (0 a 40), não só a média:

- `liga_nb` — média da liga, binomial negativa (**modelo-base** de comparação).
- `form5_pois`, `form10_nb`, `venue10_nb` — médias móveis regularizadas (forma recente e mando).
- `nb_regression` — regressão binomial negativa sobre a forma.
- `hier_pois`, `hier_nb`, `hier_nb_rapido` — **força de ataque/defesa por equipe**, mando e nível por campeonato, encolhimento hierárquico (ridge), **ponderação temporal** (meia-vida 240 ou 90 dias); totais como convolução de duas binomiais negativas; incerteza pela informação de Fisher penalizada.

Probabilidades de Over/Under tratam linhas meias, inteiras (devolução) e asiáticas (,25/,75). O EV considera devoluções; a probabilidade implícita é comparada com a do mercado **sem margem**.

### Classificação no radar

| Classe | Regra |
|---|---|
| VALOR POTENCIAL | EV central ≥ mínimo **e** EV conservador > 0 (probabilidade no extremo desfavorável da incerteza) **e** modelo superou o modelo-base no teste walk-forward (IC 95% abaixo de zero) **e** diferença para o mercado sem margem ≤ 12 p.p. |
| AGUARDAR CONFIRMAÇÃO | EV positivo que falha alguma das condições acima. |
| SEM VANTAGEM | EV central ≤ 0. |
| DADOS INSUFICIENTES | Sem odd real, odd sem horário, desatualizada ou posterior ao início, ou histórico curto das equipes. |

Com os dados atuais do football-data (5 ligas, 3 temporadas) nenhum modelo supera o modelo-base de forma conclusiva — por isso o radar não marca VALOR POTENCIAL nessas condições. Esse é o comportamento esperado de um sistema honesto.

## Configuração (Vercel → Settings → Environment Variables)

| Variável | Para quê |
|---|---|
| `THE_ODDS_API_KEY` | Odds pré-jogo de escanteios (The Odds API). Cada jogo consultado consome créditos. |
| `ODDS_API_REGIONS` | Regiões das casas (padrão `eu,uk`). |
| `ODDS_API_MAX_EVENTS` | Máximo de jogos consultados por atualização (padrão 12). |
| `DATABASE_URL` | PostgreSQL (Neon pelo Vercel Marketplace). Ativa registro no servidor e histórico de odds. |
| `APP_ACCESS_TOKEN` | Token do proprietário (≥ 16 caracteres). Necessário para ler/gravar dados privados no banco. |
| `CRON_SECRET` | Protege `/api/v4/cron/snapshot-odds` (Vercel Cron diário grava odds no banco para CLV). |

Credenciais ficam só em variáveis de ambiente; nada é gravado no repositório.

## Rodar localmente

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8765
```

Testes:

```bash
pip install httpx
python -m unittest discover -s tests -v
```

Backtest longo fora do limite serverless (e registro da versão do modelo no banco):

```bash
python scripts/backtest.py --leagues E0 SP1 D1 I1 F1 --seasons 6 --folds 16 --out relatorio.json
```

## Estrutura

| Caminho | Função |
|---|---|
| `app.py` | Entrada FastAPI (Vercel). Mantém as rotas v3. |
| `api_v4.py` | Rotas `/api/v4/*`: status, dataset, radar, confronto, backtest, apostas, cron. |
| `cqe/providers.py` | football-data.co.uk, The Odds API, CSV; estado de cada fonte. |
| `cqe/model.py` | Distribuições, EV, odds justas, modelo hierárquico de força. |
| `cqe/backtest.py` | Walk-forward, métricas, bootstrap, ROI/drawdown/CLV. |
| `cqe/analysis.py` | Projeções, perfis, radar e classificação. |
| `cqe/db.py` | PostgreSQL/SQLite com isolamento por usuário. Esquema em `docs/schema.sql`. |
| `engine.py` | Motor v3 (features de forma), reaproveitado. |
| `public/` | Interface (HTML/CSS/JS sem bibliotecas externas). |
| `docs/ARQUITETURA.md` | Arquitetura, pesquisa de provedores, limites e pendências. |

Deploy: a Vercel publica a branch `main` conectada ao repositório. Para rodar os testes no GitHub Actions a cada push, copie `docs/github-actions-tests.yml` para `.github/workflows/tests.yml` (exige um token com permissão `workflow` ou o editor do GitHub).
