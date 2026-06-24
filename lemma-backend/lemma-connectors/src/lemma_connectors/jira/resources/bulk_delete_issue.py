from __future__ import annotations

from lemma_connectors.jira.generated.tool_types import BulkDeleteIssuePropertyToolInput, BulkDeleteIssuePropertyToolOutput
from lemma_connectors.core.resource import BaseResourceClient, coerce_tool_result, operation

class BulkDeleteIssuePropertyInput(BulkDeleteIssuePropertyToolInput):
    """Operation input for `bulk_delete_issue_property`."""
    pass

class BulkDeleteIssuePropertyOutput(BulkDeleteIssuePropertyToolOutput):
    """Operation output for `bulk_delete_issue_property`."""
    pass

class JiraBulkDeleteIssueResource(BaseResourceClient):
    """Operations for the `bulk_delete_issue` resource."""

    @operation(
        name='bulk_delete_issue_property',
        title='BulkDeleteIssueProperty',
        input_model=BulkDeleteIssuePropertyInput,
        output_model=BulkDeleteIssuePropertyOutput,
        tools_used=('bulk_delete_issue_property',),
        tags=tuple(['Issue properties']),
    )
    async def property(self, data: BulkDeleteIssuePropertyInput) -> BulkDeleteIssuePropertyOutput:
        r"""Deletes a property value from multiple issues. The issues to be updated can be specified by filter criteria. The criteria the filter used to identify eligible issues are: * `entityIds` Only issues from this list are eligible. * `currentValue` Only issues with the property set to this value are eligible. If both criteria is specified, they are joined with the logical *AND*: only issues that satisfy both criteria are considered eligible. If no filter criteria are specified, all the issues visible to the user and where the user has the EDIT\_ISSUES permission for the issue are considered eligible. This operation is: * transactional, either the property is deleted from all eligible issues or, when errors occur, no properties are deleted. * [asynchronous](#async). Follow the `location` link in the response to determine the status of the task and use [Get task](#api-rest-api-3-task-taskId-get) to obtain subsequent updates. **[Permissions](#permissions) required:** * *Browse projects* [ project permission](https://confluence.atlassian.com/x/yodKLg) for each project containing issues. * If [issue-level security](https://confluence.atlassian.com/x/J4lKLg) is configured, issue-level security permission to view the issue. * *Edit issues* [project permission](https://confluence.atlassian.com/x/yodKLg) for each issue.

Important inputs: property_key, body"""
        tool = self._client.get_tool('bulk_delete_issue_property')
        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))
        return BulkDeleteIssuePropertyOutput.model_validate(coerce_tool_result(result))
