/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { PodConfig } from './PodConfig.js';
import type { PodProvisioningStatus } from './PodProvisioningStatus.js';
/**
 * Pod response schema.
 */
export type PodResponse = {
    config?: PodConfig;
    created_at: string;
    description?: (string | null);
    icon_url?: (string | null);
    id: string;
    name: string;
    organization_id: string;
    provisioning_attempts: number;
    provisioning_completed_at?: (string | null);
    provisioning_error_code?: (string | null);
    provisioning_error_type?: (string | null);
    provisioning_started_at?: (string | null);
    provisioning_status: PodProvisioningStatus;
    updated_at: string;
    user_id: string;
};
