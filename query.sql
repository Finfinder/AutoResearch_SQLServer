-- query.sql
SELECT o.*
FROM [Sales].[SalesOrderHeader] o
JOIN [Sales].[Customer] c ON o.[CustomerID] = c.[CustomerID]
WHERE o.[OrderDate] > '2024-01-01'