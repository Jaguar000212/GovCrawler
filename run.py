import sys
from portal.main import main

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        print("\n" + "="*50)
        print("CRITICAL ERROR ON STARTUP:")
        print("="*50)
        traceback.print_exc()
        print("="*50)
        input("\nPress Enter to exit...")
        sys.exit(1)