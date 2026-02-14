# Test log_event function
try:
    from portal.database import log_event
    print("log_event imported successfully")
    
    # Try to log an event
    log_event('info', None, 'Test log event')
    print("log_event executed successfully")
except Exception as e:
    print(f"Error with log_event: {e}")
    import traceback
    traceback.print_exc()