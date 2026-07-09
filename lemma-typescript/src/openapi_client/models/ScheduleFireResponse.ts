/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScheduleFireDeliveryStatus } from './ScheduleFireDeliveryStatus.js';
export type ScheduleFireResponse = {
    attempts: number;
    completed_at?: (string | null);
    created_at: string;
    error_code?: (string | null);
    error_type?: (string | null);
    id: string;
    llm_output: Record<string, any>;
    metadata: Record<string, any>;
    payload: Record<string, any>;
    schedule_id: string;
    source_event_id: string;
    started_at?: (string | null);
    status: ScheduleFireDeliveryStatus;
    target_kind: string;
    target_run_id?: (string | null);
    updated_at: string;
};

