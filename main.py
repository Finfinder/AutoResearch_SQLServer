# main.py
import json
from runner import run_query
from variants import generate_variants

def load_query():
    with open("query.sql", "r") as f:
        return f.read()

def save_results(results):
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)

def main():
    base_query = load_query()
    variants = generate_variants(base_query)

    results = []
    best_time = float("inf")
    best_query = None

    for i, query in enumerate(variants):
        print(f"Test {i+1}/{len(variants)}")

        duration, error = run_query(query)

        if error:
            print("❌ Error:", error)
            continue

        print(f"⏱️ Time: {duration:.4f}s")

        results.append({
            "query": query,
            "time": duration
        })

        if duration < best_time:
            best_time = duration
            best_query = query

    save_results(results)

    print("\n🏆 BEST RESULT:")
    print(f"{best_time:.4f}s")
    print(best_query)

if __name__ == "__main__":
    main()