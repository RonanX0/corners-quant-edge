-- Esquema PostgreSQL gerado a partir de cqe/db.py (as tabelas também são criadas automaticamente na primeira conexão).

CREATE TABLE competitions (
	code VARCHAR(16) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	country VARCHAR(80), 
	PRIMARY KEY (code)
);

CREATE TABLE matches (
	id SERIAL NOT NULL, 
	competition VARCHAR(16) NOT NULL, 
	season VARCHAR(16), 
	date TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	kickoff_utc TIMESTAMP WITH TIME ZONE, 
	home VARCHAR(120) NOT NULL, 
	away VARCHAR(120) NOT NULL, 
	source VARCHAR(80) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (competition, date, home, away)
);

CREATE TABLE model_versions (
	version VARCHAR(80) NOT NULL, 
	code VARCHAR(40) NOT NULL, 
	description TEXT, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	backtest JSON, 
	PRIMARY KEY (version)
);

CREATE TABLE odds_snapshots (
	id SERIAL NOT NULL, 
	competition VARCHAR(120) NOT NULL, 
	date VARCHAR(10) NOT NULL, 
	home VARCHAR(120) NOT NULL, 
	away VARCHAR(120) NOT NULL, 
	market VARCHAR(40) NOT NULL, 
	line FLOAT NOT NULL, 
	over_odds FLOAT NOT NULL, 
	under_odds FLOAT NOT NULL, 
	bookmaker VARCHAR(80) NOT NULL, 
	source VARCHAR(80) NOT NULL, 
	observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	fetched_at TIMESTAMP WITH TIME ZONE, 
	kickoff_utc TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (competition, date, home, away, line, bookmaker, observed_at)
);

CREATE TABLE users (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	token_sha256 VARCHAR(64) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (token_sha256)
);

CREATE TABLE bets (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	date VARCHAR(10) NOT NULL, 
	match VARCHAR(200) NOT NULL, 
	market VARCHAR(60) NOT NULL, 
	line FLOAT, 
	side VARCHAR(10), 
	odd FLOAT NOT NULL, 
	bookmaker VARCHAR(80), 
	stake FLOAT NOT NULL, 
	result VARCHAR(12) NOT NULL, 
	profit FLOAT, 
	rationale TEXT, 
	model_version VARCHAR(80), 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_bets_user_id ON bets (user_id);

CREATE TABLE match_stats (
	match_id INTEGER NOT NULL, 
	home_corners INTEGER, 
	away_corners INTEGER, 
	home_shots FLOAT, 
	away_shots FLOAT, 
	home_sot FLOAT, 
	away_sot FLOAT, 
	home_xg FLOAT, 
	away_xg FLOAT, 
	referee VARCHAR(120), 
	updated_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (match_id), 
	FOREIGN KEY(match_id) REFERENCES matches (id)
);

CREATE TABLE predictions (
	id VARCHAR(36) NOT NULL, 
	user_id VARCHAR(36) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	payload JSON NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX ix_predictions_user_id ON predictions (user_id);

CREATE TABLE teams (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	competition VARCHAR(16), 
	PRIMARY KEY (id), 
	UNIQUE (name, competition), 
	FOREIGN KEY(competition) REFERENCES competitions (code)
);
