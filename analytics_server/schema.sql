CREATE TABLE IF NOT EXISTS public.usage_logs (
    id BIGSERIAL PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    action TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_usage_logs_action
    ON public.usage_logs (action);

CREATE INDEX IF NOT EXISTS idx_usage_logs_fingerprint
    ON public.usage_logs (fingerprint);

CREATE INDEX IF NOT EXISTS idx_usage_logs_timestamp
    ON public.usage_logs (timestamp DESC);

ALTER TABLE public.usage_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY insert_usage_logs_anon
    ON public.usage_logs
    FOR INSERT
    TO anon
    WITH CHECK (true);

CREATE POLICY insert_usage_logs_authenticated
    ON public.usage_logs
    FOR INSERT
    TO authenticated
    WITH CHECK (true);

-- Allow reading playback rows scoped to the x-fingerprint request header so the
-- /stats endpoint works with the anon key. Clients can only see rows whose
-- fingerprint matches the header they send; with a service-role key RLS is
-- bypassed entirely.
CREATE POLICY select_usage_logs_scoped
    ON public.usage_logs
    FOR SELECT
    TO anon, authenticated
    USING (
        fingerprint = NULLIF(
            current_setting('request.headers', true)::json->>'x-fingerprint',
            ''
        )
    );
