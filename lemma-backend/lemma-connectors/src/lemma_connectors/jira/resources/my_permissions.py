from __future__ import annotations

from lemma_connectors.jira.generated.tool_types import GetMyPermissionsToolInput, GetMyPermissionsToolOutput
from lemma_connectors.core.resource import BaseResourceClient, coerce_tool_result, operation

class GetMyPermissionsInput(GetMyPermissionsToolInput):
    """Operation input for `get_my_permissions`."""
    pass

class GetMyPermissionsOutput(GetMyPermissionsToolOutput):
    """Operation output for `get_my_permissions`."""
    pass

class JiraMyPermissionsResource(BaseResourceClient):
    """Operations for the `my_permissions` resource."""

    @operation(
        name='get_my_permissions',
        title='GetMyPermissions',
        input_model=GetMyPermissionsInput,
        output_model=GetMyPermissionsOutput,
        tools_used=('get_my_permissions',),
        tags=tuple(['Permissions']),
    )
    async def get(self, data: GetMyPermissionsInput) -> GetMyPermissionsOutput:
        r"""Returns a list of permissions indicating which permissions the user has. Details of the user's permissions can be obtained in a global, project, issue or comment context. The user is reported as having a project permission: * in the global context, if the user has the project permission in any project. * for a project, where the project permission is determined using issue data, if the user meets the permission's criteria for any issue in the project. Otherwise, if the user has the project permission in the project. * for an issue, where a project permission is determined using issue data, if the user has the permission in the issue. Otherwise, if the user has the project permission in the project containing the issue. * for a comment, where the user has both the permission to browse the comment and the project permission for the comment's parent issue. Only the BROWSE\_PROJECTS permission is supported. If a `commentId` is provided whose `permissions` does not equal BROWSE\_PROJECTS, a 400 error will be returned. This means that users may be shown as having an issue permission (such as EDIT\_ISSUES) in the global context or a project context but may not have the permission for any or all issues. For example, if Reporters have the EDIT\_ISSUES permission a user would be shown as having this permission in the global context or the context of a project, because any user can be a reporter. However, if they are not the user who reported the issue queried they would not have EDIT\_ISSUES permission for that issue. Global permissions are unaffected by context. This operation can be accessed anonymously. **[Permissions](#permissions) required:** None.

Important inputs: project_key, project_id, issue_key, issue_id, permissions, project_uuid, project_configuration_uuid, comment_id"""
        tool = self._client.get_tool('get_my_permissions')
        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))
        return GetMyPermissionsOutput.model_validate(coerce_tool_result(result))
