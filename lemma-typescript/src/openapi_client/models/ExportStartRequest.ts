/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for starting a pod export.
 */
export type ExportStartRequest = {
    /**
     * Optional list of resource types to include (e.g. ['tables', 'agents']). Omit to export every supported resource type.
     */
    include?: (Array<string> | null);
    /**
     * Requested lifetime (seconds) of the signed download URL + archive retention. Clamped to the configured maximum; omit for the default.
     */
    ttl_seconds?: (number | null);
    /**
     * Include table row data (data.csv per table) in the bundle.
     */
    with_data?: boolean;
};

