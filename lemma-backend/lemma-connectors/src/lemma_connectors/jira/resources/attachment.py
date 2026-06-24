from __future__ import annotations

from lemma_connectors.jira.generated.tool_types import AddAttachmentToolInput, AddAttachmentToolOutput, GetAttachmentToolInput, GetAttachmentToolOutput, RemoveAttachmentToolInput, RemoveAttachmentToolOutput
from lemma_connectors.core.resource import BaseResourceClient, coerce_tool_result, operation

class AddAttachmentInput(AddAttachmentToolInput):
    """Operation input for `add_attachment`."""
    pass

class AddAttachmentOutput(AddAttachmentToolOutput):
    """Operation output for `add_attachment`."""
    pass

class GetAttachmentInput(GetAttachmentToolInput):
    """Operation input for `get_attachment`."""
    pass

class GetAttachmentOutput(GetAttachmentToolOutput):
    """Operation output for `get_attachment`."""
    pass

class RemoveAttachmentInput(RemoveAttachmentToolInput):
    """Operation input for `remove_attachment`."""
    pass

class RemoveAttachmentOutput(RemoveAttachmentToolOutput):
    """Operation output for `remove_attachment`."""
    pass

class JiraAttachmentResource(BaseResourceClient):
    """Operations for the `attachment` resource."""

    @operation(
        name='add_attachment',
        title='AddAttachment',
        input_model=AddAttachmentInput,
        output_model=AddAttachmentOutput,
        tools_used=('add_attachment',),
        tags=tuple(['Issue attachments']),
    )
    async def add(self, data: AddAttachmentInput) -> AddAttachmentOutput:
        r"""Adds one or more attachments to an issue. Attachments are posted as multipart/form-data ([RFC 1867](https://www.ietf.org/rfc/rfc1867.txt)). Note that: * The request must have a `X-Atlassian-Token: no-check` header, if not it is blocked. See [Special headers](#special-request-headers) for more information. * The name of the multipart/form-data parameter that contains the attachments must be `file`. The following examples upload a file called *myfile.txt* to the issue *TEST-123*: #### curl #### curl --location --request POST 'https://your-domain.atlassian.net/rest/api/3/issue/TEST-123/attachments' -u 'email@example.com:<api_token>' -H 'X-Atlassian-Token: no-check' --form 'file=@"myfile.txt"' #### Node.js #### // This code sample uses the 'node-fetch' and 'form-data' libraries: // https://www.npmjs.com/package/node-fetch // https://www.npmjs.com/package/form-data const fetch = require('node-fetch'); const FormData = require('form-data'); const fs = require('fs'); const filePath = 'myfile.txt'; const form = new FormData(); const stats = fs.statSync(filePath); const fileSizeInBytes = stats.size; const fileStream = fs.createReadStream(filePath); form.append('file', fileStream, {knownLength: fileSizeInBytes}); fetch('https://your-domain.atlassian.net/rest/api/3/issue/TEST-123/attachments', { method: 'POST', body: form, headers: { 'Authorization': `Basic ${Buffer.from( 'email@example.com:' ).toString('base64')}`, 'Accept': 'application/json', 'X-Atlassian-Token': 'no-check' } }) .then(response => { console.log( `Response: ${response.status} ${response.statusText}` ); return response.text(); }) .then(text => console.log(text)) .catch(err => console.error(err)); #### Java #### // This code sample uses the 'Unirest' library: // http://unirest.io/java.html HttpResponse response = Unirest.post("https://your-domain.atlassian.net/rest/api/2/issue/{issueIdOrKey}/attachments") .basicAuth("email@example.com", "") .header("Accept", "application/json") .header("X-Atlassian-Token", "no-check") .field("file", new File("myfile.txt")) .asJson(); System.out.println(response.getBody()); #### Python #### # This code sample uses the 'requests' library: # http://docs.python-requests.org import requests from requests.auth import HTTPBasicAuth import json url = "https://your-domain.atlassian.net/rest/api/2/issue/{issueIdOrKey}/attachments" auth = HTTPBasicAuth("email@example.com", "") headers = { "Accept": "application/json", "X-Atlassian-Token": "no-check" } response = requests.request( "POST", url, headers = headers, auth = auth, files = { "file": ("myfile.txt", open("myfile.txt","rb"), "application-type") } ) print(json.dumps(json.loads(response.text), sort_keys=True, indent=4, separators=(",", ": "))) #### PHP #### // This code sample uses the 'Unirest' library: // http://unirest.io/php.html Unirest\Request::auth('email@example.com', ''); $headers = array( 'Accept' => 'application/json', 'X-Atlassian-Token' => 'no-check' ); $parameters = array( 'file' => File::add('myfile.txt') ); $response = Unirest\Request::post( 'https://your-domain.atlassian.net/rest/api/2/issue/{issueIdOrKey}/attachments', $headers, $parameters ); var_dump($response) #### Forge #### // This sample uses Atlassian Forge and the `form-data` library. // https://developer.atlassian.com/platform/forge/ // https://www.npmjs.com/package/form-data import api from "@forge/api"; import FormData from "form-data"; const form = new FormData(); form.append('file', fileStream, {knownLength: fileSizeInBytes}); const response = await api.asApp().requestJira('/rest/api/2/issue/{issueIdOrKey}/attachments', { method: 'POST', body: form, headers: { 'Accept': 'application/json', 'X-Atlassian-Token': 'no-check' } }); console.log(`Response: ${response.status} ${response.statusText}`); console.log(await response.json()); Tip: Use a client library. Many client libraries have classes for handling multipart POST operations. For example, in Java, the Apache HTTP Components library provides a [MultiPartEntity](http://hc.apache.org/httpcomponents-client-ga/httpmime/apidocs/org/apache/http/entity/mime/MultipartEntity.html) class for multipart POST operations. This operation can be accessed anonymously. **[Permissions](#permissions) required:** * *Browse Projects* and *Create attachments* [ project permission](https://confluence.atlassian.com/x/yodKLg) for the project that the issue is in. * If [issue-level security](https://confluence.atlassian.com/x/J4lKLg) is configured, issue-level security permission to view the issue.

Important inputs: issue_id_or_key, body"""
        tool = self._client.get_tool('add_attachment')
        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))
        return AddAttachmentOutput.model_validate(coerce_tool_result(result))

    @operation(
        name='get_attachment',
        title='GetAttachment',
        input_model=GetAttachmentInput,
        output_model=GetAttachmentOutput,
        tools_used=('get_attachment',),
        tags=tuple(['Issue attachments']),
    )
    async def get(self, data: GetAttachmentInput) -> GetAttachmentOutput:
        """Returns the metadata for an attachment. Note that the attachment itself is not returned. This operation can be accessed anonymously. **[Permissions](#permissions) required:** * *Browse projects* [project permission](https://confluence.atlassian.com/x/yodKLg) for the project that the issue is in. * If [issue-level security](https://confluence.atlassian.com/x/J4lKLg) is configured, issue-level security permission to view the issue.

Important inputs: id"""
        tool = self._client.get_tool('get_attachment')
        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))
        return GetAttachmentOutput.model_validate(coerce_tool_result(result))

    @operation(
        name='remove_attachment',
        title='RemoveAttachment',
        input_model=RemoveAttachmentInput,
        output_model=RemoveAttachmentOutput,
        tools_used=('remove_attachment',),
        tags=tuple(['Issue attachments']),
    )
    async def remove(self, data: RemoveAttachmentInput) -> RemoveAttachmentOutput:
        """Deletes an attachment from an issue. This operation can be accessed anonymously. **[Permissions](#permissions) required:** For the project holding the issue containing the attachment: * *Delete own attachments* [project permission](https://confluence.atlassian.com/x/yodKLg) to delete an attachment created by the calling user. * *Delete all attachments* [project permission](https://confluence.atlassian.com/x/yodKLg) to delete an attachment created by any user.

Important inputs: id"""
        tool = self._client.get_tool('remove_attachment')
        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))
        return RemoveAttachmentOutput.model_validate(coerce_tool_result(result))
