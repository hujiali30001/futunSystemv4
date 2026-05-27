SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'furun' AND pid != pg_backend_pid();

ALTER TABLE arbitrage_tasks ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;
ALTER TABLE arbitrage_tasks ADD COLUMN IF NOT EXISTS max_retry_count INTEGER DEFAULT 2;
ALTER TABLE arbitrage_tasks ADD COLUMN IF NOT EXISTS cooldown_until TIMESTAMP;
ALTER TABLE arbitrage_tasks ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(255);
ALTER TABLE arbitrage_tasks ADD COLUMN IF NOT EXISTS auto_recovery_status VARCHAR(32) DEFAULT 'NONE';
