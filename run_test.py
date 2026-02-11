
import time
import random
from api_utils import retry_on_rate_limit, process_in_batches, logger

# Mock Exception for Testing
class MockRateLimitError(Exception):
    pass

# Mock API Function with failures
@retry_on_rate_limit(max_retries=5, base_delay=2.0)
def mock_api_call(items):
    """
    Simulates an API call that fails with a 429 error casually.
    """
    logger.debug(f"Calling Mock API with {len(items)} items: {items}")
    
    # Simulate random 429 error
    if random.random() < 0.7:  # 70% chance of failure to trigger retries
        logger.warning("⚠️  Mock API simulated 429 Too Many Requests!")
        raise MockRateLimitError("429 TooManyRequests: You have exceeded your quota.")
    
    time.sleep(0.5) # Simulate network work
    return f"Success for {len(items)} items"

def runTest():
    """
    Test function to verify the fix on just 3 rows of data.
    """
    logger.info("=" * 50)
    logger.info("🧪 RUNNING TEST: Verify Fix on 3 Rows")
    logger.info("=" * 50)
    
    test_rows = ["Row 1", "Row 2", "Row 3"]
    
    # Define a processor for the batch
    def process_test_batch(batch):
        mock_api_call(batch)
        
    try:
        # Process in batches of 3 (so 1 batch total)
        process_in_batches(test_rows, batch_size=3, process_func=process_test_batch)
        logger.info("✅ TEST PASSED: 3 rows processed successfully with rate limiting.")
    except Exception as e:
        logger.error(f"❌ TEST FAILED: {e}")

def run_large_batch_test():
    """
    Test function for larger datasets (e.g., 25 items, batch 10).
    """
    logger.info("\n" + "=" * 50)
    logger.info("🧪 RUNNING LOADER TEST: 25 items, batch size 10")
    logger.info("=" * 50)
    
    data = [f"Item {i+1}" for i in range(25)]
    
    def process_batch(batch):
        mock_api_call(batch)
        
    process_in_batches(data, batch_size=10, process_func=process_batch)

if __name__ == "__main__":
    # 1. Run the specific small test requested by user
    runTest()
    
    # 2. Run a larger demonstration if desired
    # run_large_batch_test()
