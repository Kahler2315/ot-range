# Cedar Hollow M4 router/sensor.
#
# Zeek's built-in Modbus analyzer (base/protocols/modbus/main.zeek)
# already gives us a real modbus.log: ts, uid, id, tid, unit, func,
# pdu_type, exception. Left untouched below — no reason to fight it.
#
# It does not carry address/quantity/values, and there's no reliable way
# to inject those into the SAME log line: main.zeek's own modbus_message
# handler (priority -5) does the Log::write, and the analyzer raises the
# generic modbus_message event before the function-specific ones (e.g.
# modbus_read_holding_registers_request) for the same PDU, so anything
# this script set from a specific-event handler would land one message
# too late. sensor/tap.py's docstring already anticipated this split —
# "Zeek's modbus.log / modbus_detailed.log" — so this script produces a
# genuinely separate modbus_detailed.log instead of fighting event order.
# sensor/detect.py's Zeek-mode reader joins the two by (uid, tid).

@load base/protocols/modbus

redef LogAscii::use_json = T;

module ModbusDetailed;

export {
	redef enum Log::ID += { LOG };

	type Info: record {
		ts:        time    &log;
		uid:       string  &log;
		id:        conn_id &log;
		tid:       count   &log &optional;
		unit:      count   &log &optional;
		func:      string  &log &optional;
		pdu_type:  string  &log &optional;
		address:   count   &log &optional;
		quantity:  count   &log &optional;
		is_write:  bool    &log &optional;
		# Space-joined, not a Zeek vector: coil reads are bool, register
		# reads are count, and a single log column can't carry both —
		# a string keeps the schema stable across function codes. The
		# consumer (sensor/detect.py) splits and retypes per func code,
		# same as it already does decoding sensor/tap.py's own output.
		values:    string  &log &optional;
	};
}

event zeek_init() &priority=5
	{
	Log::create_stream(ModbusDetailed::LOG, Log::Stream($columns=Info, $path="modbus_detailed"));
	}

function base_record(c: connection, headers: ModbusHeaders, pdu_type: string): Info
	{
	return Info(
		$ts=network_time(), $uid=c$uid, $id=c$id,
		$tid=headers$tid, $unit=headers$uid,
		$func=Modbus::function_codes[headers$function_code & ~0x80],
		$pdu_type=pdu_type
	);
	}

event modbus_read_coils_request(c: connection, headers: ModbusHeaders, start_address: count, quantity: count)
	{
	local rec = base_record(c, headers, "REQ");
	rec$address = start_address;
	rec$quantity = quantity;
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_read_discrete_inputs_request(c: connection, headers: ModbusHeaders, start_address: count, quantity: count)
	{
	local rec = base_record(c, headers, "REQ");
	rec$address = start_address;
	rec$quantity = quantity;
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_read_holding_registers_request(c: connection, headers: ModbusHeaders, start_address: count, quantity: count)
	{
	local rec = base_record(c, headers, "REQ");
	rec$address = start_address;
	rec$quantity = quantity;
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_read_input_registers_request(c: connection, headers: ModbusHeaders, start_address: count, quantity: count)
	{
	local rec = base_record(c, headers, "REQ");
	rec$address = start_address;
	rec$quantity = quantity;
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_read_coils_response(c: connection, headers: ModbusHeaders, coils: ModbusCoils)
	{
	local rec = base_record(c, headers, "RESP");
	rec$quantity = |coils|;
	local vals: string_vec = string_vec();
	for ( i in coils )
		vals[|vals|] = coils[i] ? "1" : "0";
	rec$values = join_string_vec(vals, " ");
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_read_discrete_inputs_response(c: connection, headers: ModbusHeaders, coils: ModbusCoils)
	{
	local rec = base_record(c, headers, "RESP");
	rec$quantity = |coils|;
	local vals: string_vec = string_vec();
	for ( i in coils )
		vals[|vals|] = coils[i] ? "1" : "0";
	rec$values = join_string_vec(vals, " ");
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_read_holding_registers_response(c: connection, headers: ModbusHeaders, registers: ModbusRegisters)
	{
	local rec = base_record(c, headers, "RESP");
	rec$quantity = |registers|;
	local vals: string_vec = string_vec();
	for ( i in registers )
		vals[|vals|] = cat(registers[i]);
	rec$values = join_string_vec(vals, " ");
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_read_input_registers_response(c: connection, headers: ModbusHeaders, registers: ModbusRegisters)
	{
	local rec = base_record(c, headers, "RESP");
	rec$quantity = |registers|;
	local vals: string_vec = string_vec();
	for ( i in registers )
		vals[|vals|] = cat(registers[i]);
	rec$values = join_string_vec(vals, " ");
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_write_single_coil_request(c: connection, headers: ModbusHeaders, address: count, value: bool)
	{
	local rec = base_record(c, headers, "REQ");
	rec$address = address;
	rec$quantity = 1;
	rec$is_write = T;
	rec$values = value ? "1" : "0";
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_write_single_register_request(c: connection, headers: ModbusHeaders, address: count, value: count)
	{
	local rec = base_record(c, headers, "REQ");
	rec$address = address;
	rec$quantity = 1;
	rec$is_write = T;
	rec$values = cat(value);
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_write_multiple_coils_request(c: connection, headers: ModbusHeaders, start_address: count, coils: ModbusCoils)
	{
	local rec = base_record(c, headers, "REQ");
	rec$address = start_address;
	rec$quantity = |coils|;
	rec$is_write = T;
	local vals: string_vec = string_vec();
	for ( i in coils )
		vals[|vals|] = coils[i] ? "1" : "0";
	rec$values = join_string_vec(vals, " ");
	Log::write(ModbusDetailed::LOG, rec);
	}

event modbus_write_multiple_registers_request(c: connection, headers: ModbusHeaders, start_address: count, registers: ModbusRegisters)
	{
	local rec = base_record(c, headers, "REQ");
	rec$address = start_address;
	rec$quantity = |registers|;
	rec$is_write = T;
	local vals: string_vec = string_vec();
	for ( i in registers )
		vals[|vals|] = cat(registers[i]);
	rec$values = join_string_vec(vals, " ");
	Log::write(ModbusDetailed::LOG, rec);
	}
