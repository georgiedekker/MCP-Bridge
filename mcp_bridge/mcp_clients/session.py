from datetime import timedelta
from typing import Awaitable, Callable

import anyio
from loguru import logger
import mcp.types as types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.session import BaseSession, RequestResponder
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from pydantic import AnyUrl

from mcp_bridge import __version__ as version
from mcp_bridge.sampling.sampler import handle_sampling_message
# Fix the import - import the functions before using them
from mcp_bridge.utils.message_adapter import wrap_message

sampling_function_signature = Callable[
    [types.CreateMessageRequestParams], Awaitable[types.CreateMessageResult]
]

class McpClientSession(
    BaseSession[
        types.ClientRequest,
        types.ClientNotification,
        types.ClientResult,
        types.ServerRequest,
        types.ServerNotification,
    ]
):



    def __init__(
        self,
        read_stream: MemoryObjectReceiveStream[types.JSONRPCMessage | Exception],
        write_stream: MemoryObjectSendStream[types.JSONRPCMessage],
        read_timeout_seconds: timedelta | None = None,
    ) -> None:
        super().__init__(
            read_stream,
            write_stream,
            types.ServerRequest,
            types.ServerNotification,
            read_timeout_seconds=read_timeout_seconds,
        )
        self._read_stream = read_stream
        self.incoming_messages = read_stream
        self._incoming_messages = read_stream
        # self.incoming_messages = read_stream
        # print(f"self.incoming_messages: {self.incoming_messages}")
        # print(f"self._incoming_messages: {self._incoming_messages}")

    async def __aenter__(self):
        session = await super().__aenter__()
        if not hasattr(self, 'incoming_messages') or self.incoming_messages is None:
            self.incoming_messages = self._read_stream
            self._incoming_messages = self._read_stream
            logger.debug(f"SETTING self.incoming_messages: {self.incoming_messages}")
        self._task_group.start_soon(self._consume_messages)
        return session

    async def _consume_messages(self):
        """
        Process messages from the incoming message stream, with robust error handling.
        """
        logger.info(f"McpClientSession has incoming_messages: {hasattr(self, 'incoming_messages')}")
        if hasattr(self, 'incoming_messages') and self.incoming_messages is not None:
            logger.info(f"incoming_messages type: {type(self.incoming_messages)}")
        else:
            logger.warning("incoming_messages not found or is None")
            return
            
        try:
            async for original_message in self.incoming_messages:
                try:
                    # Wrap the message
                    message = wrap_message(original_message)
                    
                    if isinstance(message, Exception):
                        logger.error(f"Received exception in message stream: {message}")
                    elif hasattr(message, 'request'):  # For RequestResponder
                        logger.debug(f"Received request: {message.request}")
                        try:
                            await self._received_request(message)
                        except Exception as req_err:
                            logger.exception(f"Error handling request: {req_err}")
                    elif hasattr(message, 'root'):
                        if hasattr(message.root, 'params') and hasattr(types, 'LoggingMessageNotification') and hasattr(types, 'ServerNotification') and isinstance(message, types.ServerNotification) and isinstance(message.root, types.LoggingMessageNotification):
                            logger.debug(f"Received notification from server: {message.root.params}")                        
                        else:
                            logger.debug(f"Received notification from server: {message}")                        
                    else:
                        logger.debug(f"Received message: {message}")
                except anyio.ClosedResourceError:
                    logger.debug("Message stream closed")
                    break
                except Exception as e:
                    logger.exception(f"Error processing message: {e}")
        except anyio.ClosedResourceError:
            logger.debug("Message stream closed")
        except Exception as e:
            logger.exception(f"Message consumer task failed: {e}")

    async def initialize(self) -> types.InitializeResult:
        result = await self.send_request(
            types.ClientRequest(
                types.InitializeRequest(
                    method="initialize",
                    params=types.InitializeRequestParams(
                        protocolVersion=types.LATEST_PROTOCOL_VERSION,
                        capabilities=types.ClientCapabilities(
                            sampling=types.SamplingCapability(),
                            experimental=None,
                            roots=types.RootsCapability(
                                listChanged=True
                            ),
                        ),
                        clientInfo=types.Implementation(name="MCP-Bridge", version=version),
                    ),
                )
            ),
            types.InitializeResult,
        )

        if result.protocolVersion not in SUPPORTED_PROTOCOL_VERSIONS:
            raise RuntimeError(
                "Unsupported protocol version from the server: "
                f"{result.protocolVersion}"
            )

        await self.send_notification(
            types.ClientNotification(
                types.InitializedNotification(method="notifications/initialized")
            )
        )

        return result

    async def send_ping(self) -> types.EmptyResult:
        """Send a ping request."""
        return await self.send_request(
            types.ClientRequest(
                types.PingRequest(
                    method="ping",
                )
            ),
            types.EmptyResult,
        )

    async def send_progress_notification(
        self, progress_token: str | int, progress: float, total: float | None = None
    ) -> None:
        """Send a progress notification."""
        await self.send_notification(
            types.ClientNotification(
                types.ProgressNotification(
                    method="notifications/progress",
                    params=types.ProgressNotificationParams(
                        progressToken=progress_token,
                        progress=progress,
                        total=total,
                    ),
                ),
            )
        )

    async def set_logging_level(self, level: types.LoggingLevel) -> types.EmptyResult:
        """Send a logging/setLevel request."""
        return await self.send_request(
            types.ClientRequest(
                types.SetLevelRequest(
                    method="logging/setLevel",
                    params=types.SetLevelRequestParams(level=level),
                )
            ),
            types.EmptyResult,
        )

    async def list_resources(self) -> types.ListResourcesResult:
        """Send a resources/list request."""
        return await self.send_request(
            types.ClientRequest(
                types.ListResourcesRequest(
                    method="resources/list",
                )
            ),
            types.ListResourcesResult,
        )

    async def read_resource(self, uri: AnyUrl) -> types.ReadResourceResult:
        """Send a resources/read request."""
        return await self.send_request(
            types.ClientRequest(
                types.ReadResourceRequest(
                    method="resources/read",
                    params=types.ReadResourceRequestParams(uri=uri),
                )
            ),
            types.ReadResourceResult,
        )

    async def subscribe_resource(self, uri: AnyUrl) -> types.EmptyResult:
        """Send a resources/subscribe request."""
        return await self.send_request(
            types.ClientRequest(
                types.SubscribeRequest(
                    method="resources/subscribe",
                    params=types.SubscribeRequestParams(uri=uri),
                )
            ),
            types.EmptyResult,
        )

    async def unsubscribe_resource(self, uri: AnyUrl) -> types.EmptyResult:
        """Send a resources/unsubscribe request."""
        return await self.send_request(
            types.ClientRequest(
                types.UnsubscribeRequest(
                    method="resources/unsubscribe",
                    params=types.UnsubscribeRequestParams(uri=uri),
                )
            ),
            types.EmptyResult,
        )

    async def call_tool(
        self, name: str, arguments: dict | None = None
    ) -> types.CallToolResult:
        """Send a tools/call request."""
        return await self.send_request(
            types.ClientRequest(
                types.CallToolRequest(
                    method="tools/call",
                    params=types.CallToolRequestParams(name=name, arguments=arguments),
                )
            ),
            types.CallToolResult,
        )

    async def list_prompts(self) -> types.ListPromptsResult:
        """Send a prompts/list request."""
        return await self.send_request(
            types.ClientRequest(
                types.ListPromptsRequest(
                    method="prompts/list",
                )
            ),
            types.ListPromptsResult,
        )

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> types.GetPromptResult:
        """Send a prompts/get request."""
        return await self.send_request(
            types.ClientRequest(
                types.GetPromptRequest(
                    method="prompts/get",
                    params=types.GetPromptRequestParams(name=name, arguments=arguments),
                )
            ),
            types.GetPromptResult,
        )

    async def complete(
        self, ref: types.ResourceReference | types.PromptReference, argument: dict
    ) -> types.CompleteResult:
        """Send a completion/complete request."""
        return await self.send_request(
            types.ClientRequest(
                types.CompleteRequest(
                    method="completion/complete",
                    params=types.CompleteRequestParams(
                        ref=ref,
                        argument=types.CompletionArgument(**argument),
                    ),
                )
            ),
            types.CompleteResult,
        )

    async def list_tools(self) -> types.ListToolsResult:
        """Send a tools/list request."""
        return await self.send_request(
            types.ClientRequest(
                types.ListToolsRequest(
                    method="tools/list",
                )
            ),
            types.ListToolsResult,
        )

    async def send_roots_list_changed(self) -> None:
        """Send a roots/list_changed notification."""
        await self.send_notification(
            types.ClientNotification(
                types.RootsListChangedNotification(
                    method="notifications/roots/list_changed",
                )
            )
        )

    async def _received_request(
        self, responder: RequestResponder["types.ServerRequest", "types.ClientResult"]
    ) -> None:
        if isinstance(responder.request.root, types.CreateMessageRequest):
            # handle create message request (sampling)
            response = await self.sample(responder.request.root.params)
            client_response = types.ClientResult(**response.model_dump())
            await responder.respond(client_response)

    async def sample(self, params: types.CreateMessageRequestParams) -> types.CreateMessageResult:
        logger.info("got sampling request from mcp server")
        resp = await handle_sampling_message(params)
        logger.info("finished sampling request from mcp server")
        return resp
    