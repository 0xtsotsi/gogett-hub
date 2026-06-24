from __future__ import annotations

from lemma_connectors.jira.generated.tool_types import CreateIssueTypeAvatarToolInput, CreateIssueTypeAvatarToolOutput
from lemma_connectors.core.resource import BaseResourceClient, coerce_tool_result, operation

class CreateIssueTypeAvatarInput(CreateIssueTypeAvatarToolInput):
    """Operation input for `create_issue_type_avatar`."""
    pass

class CreateIssueTypeAvatarOutput(CreateIssueTypeAvatarToolOutput):
    """Operation output for `create_issue_type_avatar`."""
    pass

class JiraIssueTypeAvatarResource(BaseResourceClient):
    """Operations for the `issue_type_avatar` resource."""

    @operation(
        name='create_issue_type_avatar',
        title='CreateIssueTypeAvatar',
        input_model=CreateIssueTypeAvatarInput,
        output_model=CreateIssueTypeAvatarOutput,
        tools_used=('create_issue_type_avatar',),
        tags=tuple(['Issue types']),
    )
    async def create(self, data: CreateIssueTypeAvatarInput) -> CreateIssueTypeAvatarOutput:
        r"""Loads an avatar for the issue type. Specify the avatar's local file location in the body of the request. Also, include the following headers: * `X-Atlassian-Token: no-check` To prevent XSRF protection blocking the request, for more information see [Special Headers](#special-request-headers). * `Content-Type: image/image type` Valid image types are JPEG, GIF, or PNG. For example: `curl --request POST \ --user email@example.com:<api_token> \ --header 'X-Atlassian-Token: no-check' \ --header 'Content-Type: image/< image_type>' \ --data-binary "<@/path/to/file/with/your/avatar>" \ --url 'https://your-domain.atlassian.net/rest/api/3/issuetype/{issueTypeId}'This` The avatar is cropped to a square. If no crop parameters are specified, the square originates at the top left of the image. The length of the square's sides is set to the smaller of the height or width of the image. The cropped image is then used to create avatars of 16x16, 24x24, 32x32, and 48x48 in size. After creating the avatar, use [ Update issue type](#api-rest-api-3-issuetype-id-put) to set it as the issue type's displayed avatar. **[Permissions](#permissions) required:** *Administer Jira* [global permission](https://confluence.atlassian.com/x/x4dKLg).

Important inputs: id, x, y, size, body"""
        tool = self._client.get_tool('create_issue_type_avatar')
        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))
        return CreateIssueTypeAvatarOutput.model_validate(coerce_tool_result(result))
