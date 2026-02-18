-- Migration: Create report_audit_log table
-- Description: Audit logging for report executions

CREATE TABLE IF NOT EXISTS report_audit_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL,
    user_id INT NOT NULL,
    report_id VARCHAR(100) NOT NULL,
    report_name VARCHAR(255),
    execution_time_ms INT,
    row_count INT,
    status VARCHAR(50) NOT NULL,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_company_user (company_id, user_id),
    INDEX idx_report_id (report_id),
    INDEX idx_created_at (created_at),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
