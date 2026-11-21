Username = os:getenv("RABBITMQ_DEFAULT_USER"),
Password = os:getenv("RABBITMQ_DEFAULT_PASS"),
{ok, Params} = amqp_uri:parse("amqp://" ++ Username ++ ":" ++ Password ++ "@/" ++ Vhost),
{ok, Connection} = amqp_connection:start(Params),
try
    {ok, Channel} = amqp_connection:open_channel(Connection),
    Args = [{<<"x-queue-type">>, longstr, <<"classic">>}],
    {'queue.declare_ok', <<"private">>, _, _} =
        amqp_channel:call(Channel, {'queue.declare', 0, <<"private">>, false, false, true, false, false, Args}),
    VhostBinary = list_to_binary(Vhost),
    {ok, Private} = rabbit_amqqueue:lookup(rabbit_misc:r(VhostBinary, queue, <<"private">>)),
    true = rabbit_amqqueue:is_exclusive(Private),
    false = lists:any(fun(#{<<"vhost">> := V, <<"name">> := <<"private">>}) -> V =:= VhostBinary;
                         (_) -> false
                      end, rabbit_definitions:list_queues()),
    case catch amqp_channel:call(
        Channel, {'queue.declare', 0, <<"shared">>, false, false, false, false, false, Args}
    ) of
        {'queue.declare_ok', <<"shared">>, _, _} ->
            accepted;
        {'EXIT', Reason} ->
            Message = unicode:characters_to_binary(io_lib:format("~tp", [Reason])),
            case binary:match(Message, <<"transient_nonexcl_queues">>) of
                nomatch -> unexpected_declaration_error;
                _ -> denied_by_transient_nonexcl_rule
            end
    end
after
    catch amqp_connection:close(Connection)
end.
