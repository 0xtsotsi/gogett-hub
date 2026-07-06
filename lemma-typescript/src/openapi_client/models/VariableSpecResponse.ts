/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type VariableSpecResponse = {
    default?: (string | null);
    description?: (string | null);
    kind: string;
    name: string;
    /**
     * For a connector account variable, the platform the account must belong to (e.g. 'slack'), so the importer can connect the right connector. Null for non-connector variables.
     */
    platform?: (string | null);
    required?: boolean;
};

