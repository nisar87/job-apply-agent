-- Two roles: the owner creates tables; the application connects as an unprivileged
-- role so row-level security applies to everything it does.
CREATE ROLE jobagent_owner LOGIN PASSWORD 'owner' CREATEDB;
CREATE ROLE jobagent_app LOGIN PASSWORD 'app';
CREATE DATABASE jobagent OWNER jobagent_owner;
CREATE DATABASE jobagent_test OWNER jobagent_owner;
-- Used only by tests/test_migrations.py, which empties it and rebuilds it from the migrations.
CREATE DATABASE jobagent_migrate OWNER jobagent_owner;
