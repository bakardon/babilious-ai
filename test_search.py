from search_engine import search_knowledge

query = input("Question: ")

result = search_knowledge(query)

print("\n")

print(result["source"])

print("\n")

print(result["score"])