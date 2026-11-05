# Arquitetura — Corners Quant Edge v4

## Auditoria da v3 (ponto de partida)

- FastAPI sem estado na Vercel, motor estatístico em `engine.py` (médias móveis, Poisson, binomial negativa), interface de uma página.
- Reaproveitado: parser de CSV, features de forma calculadas dia a dia (sem vazamento), regressão binomial negativa, validação de odds com horário, rotas v3 (mantidas e testadas).
- Limitações encontradas: uma única divisão treino/validação/teste (sem walk-forward); só a média e P(Over) — sem distribuição completa; nenhuma fonte real de calendário ou odds; nenhum registro de apostas; sem persistência; modelos sem força de equipe nem ponderação temporal.
- Correções já entregues antes da v4: datas dd/mm, cache, ROI sem escolha retrospectiva de cotação, limite de importação.

## Fluxo

```
Navegador (public/)  ──HTTPS──▶  Vercel Function (app.py + api_v4.py)
   localStorage:                     │
   CSVs, apostas, oportunidades      ├─ cqe/providers ─▶ football-data.co.uk (CSV, cache 1h–7d em memória e /tmp)
   (ou token p/ banco)               │                  The Odds API (cache 15 min, limite de jogos por consulta)
                                     ├─ cqe/model, cqe/backtest, cqe/analysis (cache por conjunto de dados)
                                     └─ cqe/db ─▶ PostgreSQL (opcional)
Vercel Cron (diário) ─▶ /api/v4/cron/snapshot-odds ─▶ grava odds no banco (histórico p/ CLV)
GitHub Actions (docs/github-actions-tests.yml, a ativar) ─▶ testes a cada push
scripts/backtest.py ─▶ backtests longos fora do limite serverless
```

Cada consulta é sem estado: o servidor guarda apenas caches descartáveis. Nada roda permanentemente dentro da função; trabalhos longos vão para o script/cron.

## Pesquisa de provedores (novembro de 2026)

| Provedor | Cobertura útil | Acesso | Situação no projeto |
|---|---|---|---|
| football-data.co.uk | Resultados, escanteios (HC/AC), finalizações (HS/AS/HST/AST), xG recente, árbitro; `fixtures.csv` semanal; 1X2 e gols — **sem odds de escanteios** | CSV gratuito, sem chave ("My data is free", página de fixtures) | **Integrado** (histórico + calendário). Datas `dd/mm/aa(aa)`, horários do Reino Unido convertidos para UTC. |
| The Odds API (v4) | Mercado `alternate_totals_corners` ("Total Corners (Over/Under)") via `/v4/sports/{sport}/events/{eventId}/odds`; cabeçalhos `x-requests-remaining/used/last`; 1 crédito por mercado e região | Chave (`apiKey`), planos pagos e gratuito (créditos mensais conforme o plano) | **Integrado** — exige `THE_ODDS_API_KEY`. Nomes de equipes mapeados para o histórico; não mapeados são avisados, nunca adivinhados. |
| API-Football (api-sports) | Calendário, estatísticas e odds (inclui escanteios, segundo o mercado) | Chave | **Não integrado**: a documentação oficial retornou 403 durante a pesquisa; não implementei um adaptador sem poder verificá-lo. |
| TheStatsAPI | Odds de escanteios (`match_corners`) de várias casas; parceiro citado pelo football-data | Pago, teste de 7 dias | Candidato futuro (mesma interface de provedor). |

Nenhuma raspagem de sites de apostas foi implementada.

## Banco de dados

`cqe/db.py` (SQLAlchemy Core) cria as tabelas na primeira conexão: `users`, `competitions`, `teams`, `matches`, `match_stats`, `odds_snapshots`, `model_versions`, `predictions` (oportunidades acompanhadas), `bets`. SQL em `docs/schema.sql`.

- Linhas privadas têm `user_id`; toda consulta filtra por ele. Um usuário que reutilize o id de outro cria uma linha nova — nunca sobrescreve (testado).
- Autenticação por token portador; só o SHA-256 fica no banco. `APP_ACCESS_TOKEN` cria o usuário proprietário. Contas múltiplas com cadastro/login ainda não existem.
- Sem `DATABASE_URL`, apostas e oportunidades ficam no `localStorage` do navegador.

## Limites conhecidos

- Odds de escanteios dependem de chave paga/gratuita da The Odds API; cobertura de escanteios varia por casa e campeonato.
- O calendário gratuito cobre só a semana publicada pelo football-data; jogos sem odds aparecem como DADOS INSUFICIENTES.
- Primeira carga de 5 ligas × 3 temporadas: ~8 s (download) + ~2 s (walk-forward); depois, cache.
- O modelo não usa escalações, lesões, clima, árbitro ou notícias.
- ROI histórico só existe com odds pré-jogo datadas fornecidas pelo usuário (não autenticadas externamente).

## Próximos passos sugeridos

1. Configurar `THE_ODDS_API_KEY` e `DATABASE_URL` + `APP_ACCESS_TOKEN` + `CRON_SECRET` na Vercel.
2. Deixar o cron acumular odds por algumas semanas para medir CLV real das oportunidades acompanhadas.
3. Ajustar meia-vida e regularização do modelo hierárquico **somente** no período de validação.
4. Adicionar um segundo provedor de odds (TheStatsAPI ou API-Football) após verificar a documentação.
5. Contas com cadastro e login, se o sistema for aberto a outras pessoas.
