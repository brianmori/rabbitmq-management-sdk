%% The caller binds Vhost to a disposable test vhost. Resolve credentials only
%% inside the broker, so they never enter subprocess arguments or test output.
(fun() ->
    try
        User = uri_string:quote(os:getenv("RABBITMQ_DEFAULT_USER")),
        Password = uri_string:quote(os:getenv("RABBITMQ_DEFAULT_PASS")),
        Uri = "amqp://" ++ User ++ ":" ++ Password ++
              "@localhost:5672?hostname=vhost:" ++ Vhost ++ "&sasl=plain",
        {ok, Config} = amqp10_client:parse_uri(Uri),
        {ok, Conn} = amqp10_client:open_connection(Config),
        try
            receive
                {amqp10_event, {connection, Conn, opened}} -> ok;
                {amqp10_event, {connection, Conn, {closed, _}}} -> throw(connection_closed)
            after 5000 -> throw(connection_timeout)
            end,
            {ok, Session} = amqp10_client:begin_session_sync(Conn, 5000),
            {ok, Link} = amqp10_client:attach_sender_link(
                Session, <<"sdk-publisher">>, <<"/queues/input">>, unsettled),
            receive
                {amqp10_event, {link, Link, attached}} -> ok;
                {amqp10_event, {link, Link, {attached, _}}} -> ok;
                {amqp10_event, {link, Link, {detached, _}}} -> throw(link_detached)
            after 5000 -> throw(attach_timeout)
            end,
            receive
                {amqp10_event, {link, Link, credited}} -> ok;
                {amqp10_event, {link, Link, {detached, _}}} -> throw(link_detached)
            after 5000 -> throw(credit_timeout)
            end,
            Tag = <<"delivery-proof">>,
            Message = amqp10_msg:set_properties(
                #{subject => <<"delivery.subject">>}, amqp10_msg:new(Tag, Tag, false)),
            ok = amqp10_client:send_msg(Link, Message),
            receive
                {amqp10_disposition, {accepted, Tag}} -> published;
                {amqp10_disposition, {_, Tag}} -> throw(message_rejected)
            after 5000 -> throw(confirm_timeout)
            end
        after
            catch amqp10_client:close_connection(Conn)
        end
    catch
        throw:SafeReason when is_atom(SafeReason) -> SafeReason;
        _:_ -> probe_failed
    end
end)().
