/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Promote a conversation widget into a persisted app.
 *
 * The widget's stored HTML (addressed by conversation + tool call) is wrapped
 * into a standalone document and deployed as the app's bundle — the artifact
 * is identical to what the widget showed. See docs/app-widget-unification.md.
 */
export type CreateAppFromWidgetRequest = {
    conversation_id: string;
    description?: (string | null);
    name: string;
    public_slug?: (string | null);
    tool_call_id: string;
    visibility?: (string | null);
};
