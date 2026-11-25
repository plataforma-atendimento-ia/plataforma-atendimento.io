-- Row Level Security para produção em PostgreSQL.
-- Aplicar DEPOIS de criar as tabelas, conectando como dono do banco.
-- A aplicação deve conectar com um role SEM privilégio BYPASSRLS, por exemplo:
--   CREATE ROLE app_user LOGIN PASSWORD '...' NOBYPASSRLS;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
-- A aplicação define app.tenant_id a cada transação (ver app/db.py, evento after_begin).
-- ATENÇÃO: este script ainda NÃO foi testado contra um Postgres real (rodamos em SQLite).
-- Os testes de isolamento atuais verificam a camada da aplicação; antes do beta,
-- repetir os testes A x B apontando DATABASE_URL para um Postgres com este script aplicado.

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT unnest(ARRAY[
    'users','channels','contacts','conversations','messages','inbound_events'
  ]) LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))',
      t);
  END LOOP;
END $$;

-- Pendências: o login (/auth/login) e o webhook consultam por e-mail/phone_number_id ANTES de
-- saber o tenant. Em Postgres isso exige funções SECURITY DEFINER ou um role de serviço
-- separado para essas duas consultas. A tabela tenants não tem RLS por ter só o id do próprio tenant.
