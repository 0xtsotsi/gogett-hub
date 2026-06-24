from __future__ import annotations

from lemma_connectors.jira.generated.tool_types import BulkSetIssuePropertyToolInput, BulkSetIssuePropertyToolOutput
from lemma_connectors.core.resource import BaseResourceClient, coerce_tool_result, operation

class BulkSetIssuePropertyInput(BulkSetIssuePropertyToolInput):
    """Operation input for `bulk_set_issue_property`."""
    pass

class BulkSetIssuePropertyOutput(BulkSetIssuePropertyToolOutput):
    """Operation output for `bulk_set_issue_property`."""
    pass

class JiraBulkSetIssueResource(BaseResourceClient):
    """Operations for the `bulk_set_issue` resource."""

    @operation(
        name='bulk_set_issue_property',
        title='BulkSetIssueProperty',
        input_model=BulkSetIssuePropertyInput,
        output_model=BulkSetIssuePropertyOutput,
        tools_used=('bulk_set_issue_property',),
        tags=tuple(['Issue properties']),
    )
    async def property(self, data: BulkSetIssuePropertyInput) -> BulkSetIssuePropertyOutput:
        r"""Sets a property value on multiple issues. The value set can be a constant or determined by a [Jira expression](https://developer.atlassian.com/cloud/jira/platform/jira-expressions/). Expressions must be computable with constant complexity when applied to a set of issues. Expressions must also comply with the [restrictions](https://developer.atlassian.com/cloud/jira/platform/jira-expressions/#restrictions) that apply to all Jira expressions. The issues to be updated can be specified by a filter. The filter identifies issues eligible for update using these criteria: * `entityIds` Only issues from this list are eligible. * `currentValue` Only issues with the property set to this value are eligible. * `hasProperty`: * If *true*, only issues with the property are eligible. * If *false*, only issues without the property are eligible. If more than one criteria is specified, they are joined with the logical *AND*: only issues that satisfy all criteria are eligible. If an invalid combination of criteria is provided, an error is returned. For example, specifying a `currentValue` and `hasProperty` as *false* would not match any issues (because without the property the property cannot have a value). The filter is optional. Without the filter all the issues visible to the user and where the user has the EDIT\_ISSUES permission for the issue are considered eligible. This operation is: * transactional, either all eligible issues are updated or, when errors occur, none are updated. * [asynchronous](#async). Follow the `location` link in the response to determine the status of the task and use [Get task](#api-rest-api-3-task-taskId-get) to obtain subsequent updates. **[Permissions](#permissions) required:** * *Browse projects* [project permission](https://confluence.atlassian.com/x/yodKLg) for each project containing issues. * If [issue-level security](https://confluence.atlassian.com/x/J4lKLg) is configured, issue-level security permission to view the issue. * *Edit issues* [project permission](https://confluence.atlassian.com/x/yodKLg) for each issue.

Important inputs: property_key, body"""
        tool = self._client.get_tool('bulk_set_issue_property')
        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))
        return BulkSetIssuePropertyOutput.model_validate(coerce_tool_result(result))
