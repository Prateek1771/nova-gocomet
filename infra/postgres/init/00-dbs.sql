-- One Postgres, one database per service. Runs once, on an empty data volume.
-- temporal / openfga / litellm / langfuse databases are added with their services (M1, M4, ai profile).

create role nova_owner login password 'nova_owner';   -- owns tables, runs migrations
create role nova_app   login password 'nova_app';     -- runtime: not the owner, so RLS always applies
create role keycloak   login password 'keycloak';
create role litellm    login password 'litellm';    -- gateway: virtual keys, spend, budgets, admin UI

create database nova owner nova_owner;
create database keycloak owner keycloak;
create database litellm owner litellm;

\connect nova
grant usage on schema public to nova_app;
alter default privileges for role nova_owner in schema public
  grant select, insert, update, delete on tables to nova_app;
alter default privileges for role nova_owner in schema public
  grant usage, select on sequences to nova_app;
