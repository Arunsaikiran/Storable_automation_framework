CREATE TABLE IF NOT EXISTS public.control
(
    id INTEGER GENERATED ALWAYS AS IDENTITY,
	load_type VARCHAR(50) NOT NULL,
	table_name VARCHAR(100) NOT NULL,
	validation_type VARCHAR(100) NOT NULL,
    source_schema VARCHAR(100) NOT NULL,
    source_table VARCHAR(100) NOT NULL,
    source_db VARCHAR(50) NOT NULL,
	source_query VARCHAR(1000) NOT NULL,
    target_schema VARCHAR(100) NOT NULL,
    target_table VARCHAR(100) NOT NULL,
    target_db VARCHAR(50) NOT NULL,
	target_query VARCHAR(500) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    validation_status BOOLEAN DEFAULT FALSE,
    created_by VARCHAR(100) DEFAULT CURRENT_USER,
    updated_by VARCHAR(100) DEFAULT CURRENT_USER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT control_pkey PRIMARY KEY (id))
;