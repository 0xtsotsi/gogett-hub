from __future__ import annotations

from lemma_connectors.jira.generated.tool_types import GetApproximateApplicationLicenseCountToolInput, GetApproximateApplicationLicenseCountToolOutput
from lemma_connectors.core.resource import BaseResourceClient, coerce_tool_result, operation

class GetApproximateApplicationLicenseCountInput(GetApproximateApplicationLicenseCountToolInput):
    """Operation input for `get_approximate_application_license_count`."""
    pass

class GetApproximateApplicationLicenseCountOutput(GetApproximateApplicationLicenseCountToolOutput):
    """Operation output for `get_approximate_application_license_count`."""
    pass

class JiraApproximateApplicationLicenseCountResource(BaseResourceClient):
    """Operations for the `approximate_application_license_count` resource."""

    @operation(
        name='get_approximate_application_license_count',
        title='GetApproximateApplicationLicenseCount',
        input_model=GetApproximateApplicationLicenseCountInput,
        output_model=GetApproximateApplicationLicenseCountOutput,
        tools_used=('get_approximate_application_license_count',),
        tags=tuple(['License metrics']),
    )
    async def get(self, data: GetApproximateApplicationLicenseCountInput) -> GetApproximateApplicationLicenseCountOutput:
        r"""Returns the total approximate user account for a specific `jira licence application key`. Please note this information is cached with a 7-day lifecycle and could be stale at the time of call. #### Application Key #### An application key represents a specific version of Jira. See \{@link ApplicationKey\} for details **[Permissions](#permissions) required:** *Administer Jira* [global permission](https://confluence.atlassian.com/x/x4dKLg).

Important inputs: application_key"""
        tool = self._client.get_tool('get_approximate_application_license_count')
        result = await tool.execute(data.model_dump(exclude_none=True, exclude_unset=True, by_alias=False))
        return GetApproximateApplicationLicenseCountOutput.model_validate(coerce_tool_result(result))
